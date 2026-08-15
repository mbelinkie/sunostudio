import json
import re
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

    def med_launch_fixture(self):
        path = Path(__file__).parent / "alignment samples" / "med_launch_magic.words.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_med_launch_structure_gaps_do_not_inflate_boundary_words(self):
        data = self.med_launch_fixture()
        result = self.align(data["alignedWords"], data["lyrics"])
        response = next(line for line in result["lines"]
                        if line["authored_text"] == "(Coming through for you)"
                        and line["start"] < 50.0)
        vidya = next(line for line in result["lines"]
                     if line["authored_text"].startswith("Vidyashree"))
        bridge = next(line for line in result["lines"]
                      if line["authored_text"] == "Building something meant to last")
        bridge_response = next(line for line in result["lines"]
                               if line["authored_text"] == "(Meant to last, yeah)")
        self.assertLess(response["end"], vidya["start"])
        self.assertGreaterEqual(vidya["start"], 49.5)
        self.assertLess(vidya["start"], 50.5)
        self.assertLess(bridge["end"], 103.0)
        self.assertGreater(bridge_response["start"], 104.7)
        self.assertLess(bridge_response["start"], 105.3)
        self.assertLess(bridge_response["end"], 106.0)
        self.assertIn("trimmed structure-boundary silence before first word",
                      vidya["warnings"])
        self.assertIn("trimmed structure-boundary silence after final word",
                      bridge["warnings"])
        self.assertIn("moved response before structure-boundary instrumental gap",
                      bridge_response["warnings"])

    def test_med_launch_karaoke_preserves_absolute_word_gaps(self):
        data = self.med_launch_fixture()
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
        chorus = next(line for line in dialogues if "we’" in line and "step" in line)
        fixed = next(line for line in dialogues if "fixed" in line)
        nursing = next(line for line in dialogues if "Nursing" in line)
        main_response = next(line for line in dialogues
                             if "Learning" in line and "coming" in line
                             and line.startswith("Dialogue: 0,0:01:27"))
        chorus_onsets = onsets(chorus)
        self.assertAlmostEqual(chorus_onsets["we’"], 28.138, delta=0.035)
        self.assertAlmostEqual(chorus_onsets["re"], 28.205, delta=0.035)
        self.assertAlmostEqual(chorus_onsets["building"], 28.295, delta=0.035)
        self.assertAlmostEqual(onsets(fixed)["fixed"], 57.766, delta=0.035)
        self.assertAlmostEqual(onsets(nursing)["Nursing"], 61.915, delta=0.035)
        self.assertIn("0:01:30.19", main_response)
        self.assertFalse(any("(Coming" in line and
                             line.startswith("Dialogue: 0,0:01:30")
                             for line in dialogues))
        # Every timing-only karaoke tag must own a space or a zero-width glyph;
        # otherwise libass discards it when the next tag arrives.
        self.assertNotRegex(ass, r"\{\\kf\d+\}(?=\{\\kf)")

    def test_med_launch_inline_and_standalone_parentheticals_are_gold(self):
        data = self.med_launch_fixture()
        ass, _ = app.build_karaoke_ass(data["alignedWords"],
                                       lyrics_text=data["lyrics"])
        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        chorus = next(line for line in dialogues if "we’" in line and "step" in line)
        vidya = next(line for line in dialogues if "Vidyashree" in line)
        gold = "\\1c&H0042B9F5&"
        self.assertGreater(chorus.index(gold), chorus.index("path"))
        self.assertGreater(vidya.index(gold), vidya.index("screen"))
        self.assertFalse(any("(Coming" in line and
                             not line.startswith("Dialogue: 0,0:02:15")
                             for line in dialogues))
        final_response = next(line for line in dialogues
                              if line.startswith("Dialogue: 0,0:02:15"))
        self.assertIn(gold, final_response)

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


if __name__ == "__main__":
    unittest.main()
