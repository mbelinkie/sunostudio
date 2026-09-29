import re
import tempfile
import unittest
from pathlib import Path

import suno_studio as app


def timed_lines(lines, tags=None, start=0.0, line_gap=0.15, extra_gaps=None,
                split=None):
    """Build Suno-shaped entries; line endings carry embedded newlines."""
    tags = tags or {}
    extra_gaps = extra_gaps or {}
    split = split or {}
    words, now = [], start
    for line_index, line in enumerate(lines):
        now += extra_gaps.get(line_index, 0.0)
        tokens = line.split()
        expanded = []
        for token in tokens:
            expanded.extend(split.get(token, [token]))
        for token_index, token in enumerate(expanded):
            raw = token + ("\n" if token_index == len(expanded) - 1 else " ")
            if token_index == 0 and line_index in tags:
                raw = tags[line_index] + "\n" + raw
            words.append({"word": raw, "startS": now, "endS": now + 0.32,
                          "success": True})
            now += 0.4
        now += line_gap
    return words


class LyricAlignmentTests(unittest.TestCase):
    def align(self, words, lyrics):
        return app.align_lyrics(words, lyrics, method="section")

    def test_repeated_identical_choruses_stay_chronological(self):
        lyrics = """[Verse 1]
First verse rises
[Chorus]
Go, go, go
We own the night
[Verse 2]
Second verse answers
[Chorus]
Go, go, go
We own the night"""
        lines = ["First verse rises", "Go go go", "We own the night",
                 "Second verse answers", "Go go go", "We own the night"]
        words = timed_lines(lines, {0: "[Verse 1]", 1: "[Chorus]",
                                    3: "[Verse 2]", 4: "[Chorus]"})
        result = self.align(words, lyrics)
        ranges = [section["audio_unit_range"] for section in result["sections"]]
        self.assertEqual(ranges, [[0, 1], [1, 3], [3, 4], [4, 6]])
        self.assertGreater(result["sections"][3]["confidence"], 0.9)

    def test_bad_early_section_does_not_shift_later_sections(self):
        lyrics = """[Verse]
An exact opening line
[Bridge]
Silver signals cross the sky
[Outro]
Home is waiting at the end"""
        words = timed_lines(["nonsense noise here", "Silver signals cross the sky",
                             "Home is waiting at the end"],
                            {0: "[Verse]", 1: "[Bridge]", 2: "[Outro]"})
        result = self.align(words, lyrics)
        self.assertLess(result["sections"][0]["confidence"], 0.5)
        self.assertGreater(result["sections"][1]["confidence"], 0.9)
        self.assertGreater(result["sections"][2]["confidence"], 0.9)
        self.assertEqual(result["sections"][2]["audio_unit_range"], [2, 3])

    def test_split_contraction_matches_one_authored_word(self):
        lyrics = "[Verse]\nWe're ready now"
        words = timed_lines(["We're ready now"], {0: "[Verse]"},
                            split={"We're": ["We'", "re"]})
        result = self.align(words, lyrics)
        self.assertGreater(result["lines"][0]["confidence"], 0.95)
        self.assertEqual(result["lines"][0]["matched_text"], "We're ready now")

    def test_added_ad_lib_is_local_unmatched_audio(self):
        lyrics = "[Chorus]\nWe light the way"
        words = timed_lines(["Yeah we light the way"], {0: "[Chorus]"})
        result = self.align(words, lyrics)
        self.assertIn("Yeah", result["lines"][0]["unmatched_audio_words"])
        self.assertGreater(result["lines"][0]["confidence"], 0.7)

    def test_omitted_lyric_word_is_reported(self):
        lyrics = "[Verse]\nWe chase ultraviolet dreams tonight"
        words = timed_lines(["We chase dreams tonight"], {0: "[Verse]"})
        result = self.align(words, lyrics)
        self.assertIn("ultraviolet", result["lines"][0]["skipped_lyric_text"])
        self.assertGreater(result["lines"][0]["confidence"], 0.65)

    def test_repeated_short_phrase_preserves_all_entries(self):
        lyrics = "[Chorus]\nGo, go, go"
        words = timed_lines(["go go go"], {0: "[Chorus]"})
        result = self.align(words, lyrics)
        flat = [item for group in result["groups"] for row in group for item in row]
        self.assertEqual([item["w"] for item in flat], ["go", "go", "go"])
        self.assertGreater(result["overall_confidence"], 0.95)

    def test_missing_timed_section_labels_still_aligns(self):
        lyrics = """[Verse]
Morning comes softly
[Chorus]
Sing the answer loud
[Outro]
Night returns home"""
        words = timed_lines(["Morning comes softly", "Sing the answer loud",
                             "Night returns home"])
        result = self.align(words, lyrics)
        self.assertEqual([s["audio_unit_range"] for s in result["sections"]],
                         [[0, 1], [1, 2], [2, 3]])
        self.assertGreater(result["overall_confidence"], 0.95)

    def test_only_middle_low_confidence_section_is_hidden(self):
        lyrics = """[Verse]
Strong opening words
[Bridge]
The lantern knows the hidden road
[Outro]
Strong closing words"""
        words = timed_lines(["Strong opening words", "zip zap ad lib noise",
                             "Strong closing words"],
                            {0: "[Verse]", 1: "[Bridge]", 2: "[Outro]"})
        result = self.align(words, lyrics)
        self.assertEqual(result["sections"][1]["method"],
                         "hidden-low-confidence")
        self.assertEqual(result["sections"][0]["method"], "local-char")
        self.assertEqual(result["sections"][2]["method"], "local-char")
        self.assertEqual(len(result["groups"]), 2)
        rendered = [app.join_words([item["w"] for item in group[0]])
                    for group in result["groups"]]
        self.assertEqual(rendered, ["Strong opening words", "Strong closing words"])
        self.assertIn("full artwork retained",
                      " ".join(result["sections"][1]["warnings"]))
        ass, count = app.build_karaoke_ass(words, lyrics_text=lyrics,
                                           aligner_method="section")
        self.assertEqual(count, 2)
        self.assertNotIn("zip", ass)

    def test_long_instrumental_gap_does_not_create_fake_lyrics(self):
        lyrics = """[Verse]
Before the silence
[Outro]
After the silence"""
        words = timed_lines(["Before the silence", "After the silence"],
                            {0: "[Verse]", 1: "[Outro]"},
                            extra_gaps={1: 12.0})
        result = self.align(words, lyrics)
        self.assertEqual(len(result["groups"]), 2)
        first_end = result["groups"][0][0][-1]["e"]
        second_start = result["groups"][1][0][0]["s"]
        self.assertGreater(second_start - first_end, 10.0)
        self.assertGreater(result["sections"][1]["confidence"], 0.9)

    def test_unwritten_opening_vocalization_does_not_trigger_lyrics_early(self):
        lyrics = "[Verse]\nThe morning starts right now"
        words = timed_lines(["ooooooooh The morning starts right now"],
                            {0: "[Verse]"})
        result = self.align(words, lyrics)
        first_group = result["groups"][0][0]
        self.assertEqual(first_group[0]["w"], "The")
        self.assertEqual(first_group[0]["s"], words[1]["startS"])
        self.assertIn("ooooooooh", result["lines"][0]["unmatched_audio_words"])
        self.assertIn("unmatched vocalization excluded from subtitle onset",
                      result["lines"][0]["warnings"])

    def test_authored_parenthetical_gets_distinct_ass_colors(self):
        lyrics = "[Chorus]\nMain vocal line\n(Background vocal response)"
        words = timed_lines(["Main vocal line", "Background vocal response"],
                            {0: "[Chorus]"})
        ass, count = app.build_karaoke_ass(words, lyrics_text=lyrics,
                                           aligner_method="section")
        dialogues = [line for line in ass.splitlines()
                     if line.startswith("Dialogue:")]
        self.assertEqual(count, 2)
        self.assertNotIn("\\1c&H0042B9F5&", dialogues[0])
        self.assertIn("\\1c&H0042B9F5&", dialogues[1])
        self.assertIn("\\2c&H0080BFE0&", dialogues[1])

    def test_subsecond_lyric_block_is_diagnosed_but_not_rendered(self):
        lyrics = "[Verse]\nA proper lyric line\n(Too fast)"
        words = timed_lines(["A proper lyric line", "Too fast"],
                            {0: "[Verse]"})
        result = self.align(words, lyrics)
        response = next(line for line in result["lines"]
                        if line["authored_text"] == "(Too fast)")
        self.assertIn("renderer hides block", " ".join(response["warnings"]))
        ass, count = app.build_karaoke_ass(words, lyrics_text=lyrics,
                                           aligner_method="section")
        self.assertEqual(count, 1)
        self.assertNotIn("Too", ass)

    def test_ass_lyrics_sit_in_lower_calm_band(self):
        lyrics = "[Verse]\nCentered lyric"
        words = timed_lines(["Centered lyric"], {0: "[Verse]"})
        old = app.CONFIG.get("lyric_y")
        try:
            app.CONFIG["lyric_y"] = 0.680
            ass, _ = app.build_karaoke_ass(words, lyrics_text=lyrics)
        finally:
            app.CONFIG["lyric_y"] = old
        style = next(line for line in ass.splitlines() if line.startswith("Style: Now,"))
        self.assertIn(",56,56,734,1", style)

    def test_newline_bound_opener_moves_to_parenthetical_line(self):
        lyrics = "[Verse]\nWe are feeling fine\n(So fine)"
        words = [
            {"word": "[Verse]\nWe ", "startS": 0.0, "endS": 0.3},
            {"word": "are ", "startS": 0.4, "endS": 0.7},
            {"word": "feeling ", "startS": 0.8, "endS": 1.1},
            {"word": "fine\n\n(", "startS": 1.2, "endS": 1.7},
            {"word": "So ", "startS": 1.72, "endS": 2.0},
            {"word": "fine)\n", "startS": 2.05, "endS": 2.4},
        ]
        result = self.align(words, lyrics)
        texts = [app.join_words([item["w"] for item in group[0]])
                 for group in result["groups"]]
        self.assertEqual(texts, ["We are feeling fine", "(So fine)"])
        self.assertTrue(all(item.get("parenthetical")
                            for item in result["groups"][1][0]))

    def structure_gap_fixture(self):
        """Synthetic timing data with the boundary cases from real tracks.

        Keep this fixture self-contained: real songs and their authored lyrics
        belong in the local-only diagnostic samples, not the public test suite.
        """
        lyrics = """[Verse]
Opening line
[Chorus]
We’re building the path (step by step)
[Verse]
Signal arrives
[Bridge]
Building something meant to last
(Answer line)
[Chorus]
Closing line"""
        words = [
            {"word": "[Verse]\nOpening ", "startS": 10.0, "endS": 10.3},
            {"word": "line\n", "startS": 10.35, "endS": 10.7},
            {"word": "[Chorus]\nWe’", "startS": 28.138, "endS": 28.195},
            {"word": "re ", "startS": 28.205, "endS": 28.275},
            {"word": "building ", "startS": 28.295, "endS": 28.62},
            {"word": "the ", "startS": 28.63, "endS": 28.75},
            {"word": "path ", "startS": 28.76, "endS": 28.98},
            {"word": "(step ", "startS": 29.0, "endS": 29.2},
            {"word": "by ", "startS": 29.21, "endS": 29.35},
            {"word": "step)\n", "startS": 29.36, "endS": 29.6},
            {"word": "[Verse]\nSignal ", "startS": 49.8, "endS": 50.1},
            {"word": "arrives\n", "startS": 50.12, "endS": 50.42},
            {"word": "[Bridge]\nBuilding ", "startS": 101.7, "endS": 102.0},
            {"word": "something ", "startS": 102.03, "endS": 102.2},
            {"word": "meant ", "startS": 102.23, "endS": 102.4},
            {"word": "to ", "startS": 102.43, "endS": 102.56},
            {"word": "last\n", "startS": 102.6, "endS": 102.8},
            {"word": "\n(Answer ", "startS": 104.9, "endS": 105.2},
            {"word": "line)\n", "startS": 105.23, "endS": 106.25},
            {"word": "[Chorus]\nClosing ", "startS": 135.0, "endS": 135.3},
            {"word": "line\n", "startS": 135.35, "endS": 135.7},
        ]
        return {"lyrics": lyrics, "alignedWords": words}

    def test_structure_gaps_do_not_inflate_boundary_words(self):
        data = self.structure_gap_fixture()
        result = self.align(data["alignedWords"], data["lyrics"])
        signal = next(line for line in result["lines"]
                      if line["authored_text"] == "Signal arrives")
        bridge = next(line for line in result["lines"]
                      if line["authored_text"] == "Building something meant to last")
        bridge_response = next(line for line in result["lines"]
                               if line["authored_text"] == "(Answer line)")
        self.assertGreaterEqual(signal["start"], 49.5)
        self.assertLess(signal["start"], 50.5)
        self.assertLess(bridge["end"], 103.0)
        self.assertGreater(bridge_response["start"], 104.7)
        self.assertLess(bridge_response["start"], 105.3)
        self.assertLess(bridge_response["end"], 106.5)
        self.assertLess(signal["end"], bridge["start"])
        self.assertLess(bridge["end"], bridge_response["start"])

    def test_karaoke_preserves_absolute_word_gaps(self):
        data = self.structure_gap_fixture()
        ass, _ = app.build_karaoke_ass(data["alignedWords"],
                                       lyrics_text=data["lyrics"])

        def seconds(value):
            h, m, s = value.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)

        def onsets(dialogue):
            start = seconds(dialogue.split(",", 3)[1])
            elapsed, found = 0.0, {}
            for match in re.finditer(r"\{\\kf(\d+)\}([^\{]*)", dialogue):
                text = match.group(2).replace("\u200b", "").strip()
                if text:
                    found[text] = start + elapsed
                elapsed += int(match.group(1)) / 100.0
            return found

        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        chorus = next(line for line in dialogues if "We’" in line and "step" in line)
        chorus_onsets = onsets(chorus)
        self.assertAlmostEqual(chorus_onsets["We’"], 28.138, delta=0.035)
        self.assertAlmostEqual(chorus_onsets["re"], 28.205, delta=0.035)
        self.assertAlmostEqual(chorus_onsets["building"], 28.295, delta=0.035)
        # Every timing-only karaoke tag must own a space or a zero-width glyph;
        # otherwise libass discards it when the next tag arrives.
        self.assertNotRegex(ass, r"\{\\kf\d+\}(?=\{\\kf)")

    def test_inline_and_standalone_parentheticals_are_gold(self):
        data = self.structure_gap_fixture()
        ass, _ = app.build_karaoke_ass(data["alignedWords"],
                                       lyrics_text=data["lyrics"])
        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        chorus = next(line for line in dialogues if "We’" in line and "step" in line)
        response = next(line for line in dialogues if "Answer" in line)
        gold = "\\1c&H0042B9F5&"
        self.assertGreater(chorus.index(gold), chorus.index("path"))
        self.assertIn(gold, response)

    def test_hybrid_method_keeps_section_aligner_as_text_only_fallback(self):
        lyrics = "[Verse]\nA strong local line"
        words = timed_lines(["A strong local line"], {0: "[Verse]"})
        result = app.align_lyrics(words, lyrics, method="stable-ts-hybrid")
        self.assertEqual(result["method"], "section-dp-local-char")
        self.assertGreater(result["overall_confidence"], 0.9)

    def test_audio_grouping_preserves_hybrid_parenthetical_color(self):
        words = [{"word": "\n(So", "startS": 1.0, "endS": 1.5,
                  "parenthetical": True},
                 {"word": "fine)", "startS": 1.5, "endS": 2.1,
                  "parenthetical": True}]
        ass, rendered = app.build_karaoke_ass(words, lyrics_text="")
        self.assertEqual(rendered, 1)
        self.assertIn("\\1c&H0042B9F5&", ass)


class TimingSidecarTests(unittest.TestCase):
    """The TSV is deliberately tested at the renderer-group boundary."""

    def trivia_groups(self):
        words = [
            ("It’s", 20.43, 20.65), ("time", 20.65, 20.99),
            ("to", 20.99, 21.41), ("press", 21.41, 21.61),
            ("play", 21.61, 22.01), ("on", 22.01, 22.45),
            ("a", 22.45, 22.79), ("groovy", 22.79, 23.17),
            ("design!", 23.17, 24.31), ("trivia", 26.13, 27.35),
        ]
        later = [("Later", 30.00, 30.30), ("section", 30.31, 30.80),
                 ("stays", 30.81, 31.20)]
        return [[[{"w": text, "s": start, "e": end}
                  for text, start, end in words]],
                [[{"w": text, "s": start, "e": end}
                  for text, start, end in later]]]

    def sidecar_path(self, groups):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "song.timings.tsv"
        app.export_timing_sidecar(path, groups)
        return path

    def replace_timing(self, path, text, start=None, end=None, replacement_text=None):
        rows = path.read_text(encoding="utf-8").splitlines()
        for index, row in enumerate(rows[1:], 1):
            columns = row.split("\t")
            if columns[-1] == text:
                if start is not None:
                    columns[2] = start
                if end is not None:
                    columns[3] = end
                if replacement_text is not None:
                    columns[4] = replacement_text
                rows[index] = "\t".join(columns)
                path.write_text("\n".join(rows) + "\n", encoding="utf-8")
                return
        self.fail(f"missing TSV word {text!r}")

    def test_sidecar_round_trip_and_unicode_are_stable(self):
        groups = self.trivia_groups()
        path = self.sidecar_path(groups)
        original = path.read_text(encoding="utf-8")
        self.assertIn("It’s", original)
        revised = app.apply_timing_sidecar(path, groups)
        self.assertEqual(app.timing_sidecar_text(revised), original)

    def test_moving_trivia_earlier_keeps_other_words_and_sections_fixed(self):
        fixture = (Path(__file__).parent / "alignment samples" /
                   "timing_sidecar_trivia.ass").read_text(encoding="utf-8")
        self.assertIn("{\\kf182} {\\kf122}trivia", fixture)
        groups = self.trivia_groups()
        path = self.sidecar_path(groups)
        self.replace_timing(path, "trivia", start="00:00:26.000")
        revised = app.apply_timing_sidecar(path, groups)
        self.assertEqual(revised[0][0][8]["s"], 23.17)
        self.assertEqual(revised[0][0][9]["s"], 26.0)
        self.assertEqual(revised[1][0][0]["s"], 30.0)
        before, _ = app.build_karaoke_ass([], timing_groups=groups)
        after, _ = app.build_karaoke_ass([], timing_groups=revised)
        before_dialogues = [line for line in before.splitlines()
                            if line.startswith("Dialogue:")]
        after_dialogues = [line for line in after.splitlines()
                           if line.startswith("Dialogue:")]
        self.assertIn("{\\kf114}design!{\\kf169} {\\kf135}trivia", after_dialogues[0])
        self.assertEqual(after_dialogues[0].split(",", 9)[:9],
                         before_dialogues[0].split(",", 9)[:9])
        self.assertEqual(after_dialogues[1], before_dialogues[1])

    def test_moving_one_word_later_does_not_shift_later_words(self):
        groups = self.trivia_groups()
        path = self.sidecar_path(groups)
        self.replace_timing(path, "trivia", start="00:00:26.200")
        revised = app.apply_timing_sidecar(path, groups)
        self.assertEqual(revised[0][0][9]["s"], 26.2)
        self.assertEqual(revised[1][0][0]["s"], groups[1][0][0]["s"])

    def test_malformed_timestamps_report_the_sidecar_line(self):
        groups = self.trivia_groups()
        path = self.sidecar_path(groups)
        self.replace_timing(path, "trivia", start="26 seconds")
        with self.assertRaisesRegex(ValueError, r"sidecar line 11: timestamp must"):
            app.apply_timing_sidecar(path, groups)

    def test_overlapping_or_reversed_intervals_are_rejected(self):
        groups = self.trivia_groups()
        path = self.sidecar_path(groups)
        self.replace_timing(path, "trivia", start="00:00:24.000")
        with self.assertRaisesRegex(ValueError, r"sidecar line 11: start overlaps"):
            app.apply_timing_sidecar(path, groups)
        path = self.sidecar_path(groups)
        self.replace_timing(path, "trivia", start="00:00:27.350",
                            end="00:00:27.350")
        with self.assertRaisesRegex(ValueError, r"sidecar line 11: end must be after start"):
            app.apply_timing_sidecar(path, groups)

    def test_stale_mismatched_sidecar_is_rejected(self):
        groups = self.trivia_groups()
        path = self.sidecar_path(groups)
        self.replace_timing(path, "trivia", replacement_text="Trivia")
        with self.assertRaisesRegex(ValueError, r"sidecar line 11: text does not match"):
            app.apply_timing_sidecar(path, groups)

    def test_missing_or_duplicate_ids_are_rejected(self):
        groups = self.trivia_groups()
        path = self.sidecar_path(groups)
        rows = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"sidecar is missing line_id 2 word_id 3"):
            app.apply_timing_sidecar(path, groups)
        path = self.sidecar_path(groups)
        rows = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(rows + [rows[1]]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"duplicate line_id 1 word_id 1"):
            app.apply_timing_sidecar(path, groups)

    def test_absolute_centisecond_rounding_is_local_and_deterministic(self):
        groups = [[[{"w": "A", "s": 1.005, "e": 1.015},
                    {"w": "B", "s": 1.025, "e": 2.035}]]]
        path = self.sidecar_path(groups)
        revised = app.apply_timing_sidecar(path, groups)
        ass, _ = app.build_karaoke_ass([], timing_groups=revised)
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        self.assertIn("0:00:00.83", dialogue)
        self.assertIn("{\\kf18}", dialogue)
        self.assertIn("{\\kf1}A{\\kf1} {\\kf101}B", dialogue)

    def test_existing_ass_converts_to_tsv_and_back_with_an_edited_gap(self):
        source = (Path(__file__).parent / "alignment samples" /
                  "timing_sidecar_trivia.ass").read_text(encoding="utf-8")
        groups = app.karaoke_groups_from_ass(source)
        self.assertEqual([item["w"] for item in groups[0][0]][:2], ["It’s", "time"])
        self.assertEqual(groups[0][0][-1]["s"], 26.13)
        sidecar = app.timing_sidecar_text(groups).replace(
            "00:00:26.130\t00:00:27.350\ttrivia",
            "00:00:26.000\t00:00:27.350\ttrivia")
        revised = app.apply_timing_sidecar_text(sidecar, groups)
        rendered = app.render_ass_timing_sidecar(source, revised)
        self.assertIn("Dialogue: 0,0:00:20.43,0:00:27.79,Now", rendered)
        self.assertIn("{\\kf114}design!{\\kf169} {\\kf135}trivia", rendered)

    def test_in_progress_review_converts_and_saves_a_timing_override(self):
        source = app.ASS_HEAD.format(font="Helvetica", size=56, hi="&H00A6D322",
                                     lo="&H00C8C8C8", margin=56, vmargin=734) + \
            (Path(__file__).parent / "alignment samples" /
             "timing_sidecar_trivia.ass").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "song.ass"
            override = Path(directory) / "song.edited.ass"
            generated.write_text(source, encoding="utf-8")
            job = {"subtitle_generated_path": str(generated),
                   "subtitle_override_path": str(override)}
            sidecar, path, edited = app.subtitle_timing_text(job)
            self.assertFalse(edited)
            self.assertEqual(path.name, "song.timings.tsv")
            changed = sidecar.replace("00:00:26.130\t00:00:27.350\ttrivia",
                                      "00:00:26.000\t00:00:27.350\ttrivia")
            self.assertEqual(app.save_subtitle_timing_text(job, changed), 10)
            self.assertIn("{\\kf169} {\\kf135}trivia",
                          override.read_text(encoding="utf-8"))


class DisplayLyricsTests(unittest.TestCase):
    def test_email_pair_and_validation(self):
        body = ("===LYRICS===\n[Verse]\nTake the G-MAT and S-A-T\n"
                "===DISPLAY LYRICS===\n[Verse]\nTake the GMAT and SAT")
        form = app.parse_request("Exams", body)
        self.assertIn("G-MAT", form["lyrics"])
        self.assertIn("GMAT", form["display_lyrics"])
        app.validate_display_lyrics(form["lyrics"], form["display_lyrics"])
        with self.assertRaisesRegex(ValueError, "same sections and lines"):
            app.validate_display_lyrics(form["lyrics"], "[Verse]\nTake the GMAT and SAT\nExtra")
        with self.assertRaisesRegex(ValueError, "same sung letters"):
            app.validate_display_lyrics(form["lyrics"], "[Verse]\nTake the GMAT and ACT")
        self.assertIsNone(app.parse_request("Old", "===LYRICS===\nHello")["display_lyrics"])
        with self.assertRaisesRegex(ValueError, "block is empty"):
            app.validate_display_lyrics(form["lyrics"], "")
        before = len(app.JOBS)
        with self.assertRaisesRegex(ValueError, "same sung letters"):
            app.start_job({**form, "display_lyrics": "[Verse]\nTake the GMAT and ACT"})
        self.assertEqual(len(app.JOBS), before)

    def test_split_acronyms_become_single_timed_highlights(self):
        spoken = "[Verse]\nTake the G-MAT and S-A-T today"
        shown = "[Verse]\nTake the GMAT and SAT today"
        words = timed_lines(["Take the G-MAT and S-A-T today"], {0: "[Verse]"},
                            split={"G-MAT": ["G", "MAT"],
                                   "S-A-T": ["S", "A", "T"]})
        groups = app.karaoke_groups(words, spoken, display_lyrics=shown)
        items = groups[0][0]
        self.assertEqual([item["w"] for item in items],
                         ["Take", "the", "GMAT", "and", "SAT", "today"])
        self.assertEqual((items[2]["s"], items[2]["e"]), (words[2]["startS"], words[3]["endS"]))
        self.assertEqual((items[4]["s"], items[4]["e"]), (words[5]["startS"], words[7]["endS"]))
        self.assertIn("\tGMAT", app.timing_sidecar_text(groups))
        ass, count = app.build_karaoke_ass(words, lyrics_text=spoken,
                                           display_lyrics=shown)
        self.assertEqual(count, 1)
        self.assertIn("GMAT", ass)
        self.assertNotIn("G-MAT", ass)

    def test_changed_letter_pronunciations_keep_word_timing(self):
        spoken = "[Verse]\nShip JAY-SON with R-AND-B today"
        shown = "[Verse]\nShip JSON with R&B today"
        app.validate_display_lyrics(spoken, shown)
        words = timed_lines(["Ship JAY-SON with R-AND-B today"], {0: "[Verse]"},
                            split={"JAY-SON": ["JAY", "SON"],
                                   "R-AND-B": ["R", "AND", "B"]})
        warnings = []
        groups = app.karaoke_groups(words, spoken, display_lyrics=shown,
                                    display_warnings=warnings)
        items = groups[0][0]
        self.assertEqual([item["w"] for item in items],
                         ["Ship", "JSON", "with", "R&B", "today"])
        self.assertEqual((items[1]["s"], items[1]["e"]),
                         (words[1]["startS"], words[2]["endS"]))
        self.assertEqual((items[3]["s"], items[3]["e"]),
                         (words[4]["startS"], words[6]["endS"]))
        self.assertEqual(warnings, [])
        with self.assertRaisesRegex(ValueError, "same sung letters"):
            app.validate_display_lyrics(spoken, shown.replace("JSON", "JASON"))

    def test_slack_id_is_metadata_only(self):
        form = app.parse_request("Song", "===EMAIL===\na@example.com\n"
                                 "===SLACK ID===\nC01234567\n===LYRICS===\nHello")
        self.assertEqual(form["slack_channel_id"], "C01234567")
        self.assertEqual(form["delivery_mode"], "none")
        self.assertEqual(form["lyrics"], "Hello")

    def test_uncertain_line_uses_local_display_fallback(self):
        groups = [[[{"w": "Gee", "s": 1.0, "e": 1.3},
                    {"w": "Mat", "s": 1.4, "e": 1.8}]],
                  [[{"w": "Next", "s": 2.0, "e": 2.4}]]]
        diagnostics = {"lines": [
            {"section_index": 0, "line_index": 0, "start": 1.0, "end": 1.8},
            {"section_index": 0, "line_index": 1, "start": 2.0, "end": 2.4}]}
        warnings = []
        shown = app.display_karaoke_groups(groups, "G-MAT\nNext", "GMAT\nNext",
                                            diagnostics, warnings)
        self.assertEqual(shown[0][0][0]["w"], "GMAT")
        self.assertEqual(shown[1], groups[1])
        self.assertEqual(len(warnings), 1)

    def test_stable_ts_keeps_original_display_line_indices(self):
        display = "[Verse]\nGMAT first\nSAT second\nFinal line"
        hybrid = {"lines": [{"hidden": False}, {"hidden": True}, {"hidden": False}]}
        self.assertEqual(app.safe_display_lyrics(display, hybrid), "GMAT first\nFinal line")


if __name__ == "__main__":
    unittest.main()
