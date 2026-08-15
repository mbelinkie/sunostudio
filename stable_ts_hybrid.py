#!/usr/bin/env python3
"""Local stable-ts forced alignment with bounded per-line repair.

This helper intentionally runs outside Suno Studio's standard-library process.
It is invoked with the Python interpreter in ~/.suno_studio/stable-ts-venv.
"""

import argparse
import difflib
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import stable_whisper


WHISPER_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"


def norm(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def authored_document(lyrics):
    lines, section_ids = [], []
    section_id = 0
    saw_content = False
    for raw in str(lyrics).splitlines():
        text = raw.strip()
        if not text or set(text) == {"-"}:
            continue
        if re.fullmatch(r"\[[^\]]+\]", text):
            if saw_content:
                section_id += 1
            saw_content = False
            continue
        lines.append(text)
        section_ids.append(section_id)
        saw_content = True
    return lines, section_ids


def word_rows(payload):
    rows = []
    for segment in (payload or {}).get("segments") or []:
        for word in segment.get("words") or []:
            if word.get("start") is None or word.get("end") is None:
                continue
            rows.append({"word": str(word.get("word") or "").strip(),
                         "start": float(word["start"]),
                         "end": float(word["end"]),
                         "probability": float(word.get("probability") or 0.0)})
    return [row for row in rows if row["word"]]


def cloud_word_rows(payload):
    rows = []
    for word in (payload or {}).get("words") or []:
        if word.get("start") is None or word.get("end") is None:
            continue
        text = str(word.get("word") or "").strip()
        if text:
            rows.append({"word": text, "start": float(word["start"]),
                         "end": float(word["end"]), "probability": 0.0})
    return rows


def suno_rows(rows):
    output = []
    for row in rows or []:
        try:
            start, end = float(row["startS"]), float(row["endS"])
        except (KeyError, TypeError, ValueError):
            continue
        text = re.sub(r"\[[^\]]+\]", " ", str(row.get("word") or ""))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            output.append({"word": text, "start": start, "end": max(start, end),
                           "probability": 0.0})
    return output


def line_quality(segment):
    words = segment.get("words") or []
    probabilities = [float(word.get("probability") or 0.0) for word in words]
    gaps = [float(words[i]["start"]) - float(words[i - 1]["end"])
            for i in range(1, len(words))]
    mean_probability = sum(probabilities) / len(probabilities) if probabilities else 0.0
    maximum_gap = max(gaps, default=0.0)
    return mean_probability, maximum_gap


def best_candidate(words, target, lo, hi, expected_count, source):
    local = [word for word in words
             if word["end"] >= lo and word["start"] <= hi]
    best = None
    for start in range(len(local)):
        for count in range(max(1, expected_count - 3), expected_count + 4):
            candidate = local[start:start + count]
            if len(candidate) != count:
                continue
            text = " ".join(word["word"] for word in candidate)
            similarity = difflib.SequenceMatcher(
                None, norm(target), norm(text), autojunk=False).ratio()
            gaps = [candidate[i]["start"] - candidate[i - 1]["end"]
                    for i in range(1, len(candidate))]
            maximum_gap = max(gaps, default=0.0)
            maximum_word_duration = max(
                (word["end"] - word["start"] for word in candidate), default=0.0)
            outside = max(0.0, lo - candidate[0]["start"]) + \
                max(0.0, candidate[-1]["end"] - hi)
            score = similarity + (0.05 if count == expected_count else 0.0)
            score -= min(0.35, max(0.0, maximum_gap - 0.8) * 0.10)
            score -= min(0.30, outside * 0.10)
            score -= min(0.35, max(0.0, maximum_word_duration - 2.5) * 0.12)
            row = {"score": score, "similarity": similarity,
                   "maximum_internal_gap": maximum_gap,
                   "maximum_word_duration": maximum_word_duration, "words": candidate,
                   "text": text, "source": source}
            if best is None or row["score"] > best["score"]:
                best = row
    return best


def remap(authored, candidate):
    if len(authored) == len(candidate):
        return [{"word": lyric["word"], "start": float(audio["start"]),
                 "end": float(audio["end"]),
                 "probability": float(audio.get("probability") or 0.0)}
                for lyric, audio in zip(authored, candidate)]
    start, end = float(candidate[0]["start"]), float(candidate[-1]["end"])
    weights = [max(1, len(norm(word.get("word") or ""))) for word in authored]
    total = sum(weights)
    cursor, output = start, []
    for index, (word, weight) in enumerate(zip(authored, weights)):
        word_end = end if index == len(weights) - 1 else \
            cursor + (end - start) * weight / total
        output.append({"word": word["word"], "start": cursor, "end": word_end,
                       "probability": 0.0})
        cursor = word_end
    return output


def transcribe_window(model, audio, ffmpeg, lo, hi, temp_dir):
    clip = Path(temp_dir) / f"window-{lo:.3f}-{hi:.3f}.wav"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{lo:.3f}", "-to", f"{hi:.3f}", "-i", str(audio),
                    "-vn", "-ac", "1", "-ar", "16000", str(clip)], check=True)
    result = model.transcribe(
        str(clip), language="en", word_timestamps=True,
        condition_on_previous_text=False, suppress_silence=True,
        verbose=False)
    rows = word_rows(result.to_dict())
    for row in rows:
        row["start"] += lo
        row["end"] += lo
    return rows


def cloud_transcribe_window(key, audio, ffmpeg, lo, hi, temp_dir):
    """Independently hear one bounded window with hosted whisper-1.

    The API key arrives only through the child process environment. It is never
    written into the request JSON, cache, diagnostic output, or command line.
    """
    clip = Path(temp_dir) / f"cloud-{lo:.3f}-{hi:.3f}.wav"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{lo:.3f}", "-to", f"{hi:.3f}", "-i", str(audio),
                    "-vn", "-ac", "1", "-ar", "16000", str(clip)], check=True)
    boundary = "----suno-window-" + uuid.uuid4().hex
    chunks = []
    fields = [("model", "whisper-1"), ("language", "en"),
              ("response_format", "verbose_json"),
              ("timestamp_granularities[]", "word"), ("temperature", "0")]
    for name, value in fields:
        chunks.extend([f"--{boundary}\r\n".encode(),
                       f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                       str(value).encode(), b"\r\n"])
    mime = mimetypes.guess_type(clip.name)[0] or "audio/wav"
    chunks.extend([f"--{boundary}\r\n".encode(),
                   (f'Content-Disposition: form-data; name="file"; '
                    f'filename="{clip.name}"\r\n').encode(),
                   f"Content-Type: {mime}\r\n\r\n".encode(),
                   clip.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
    request = urllib.request.Request(
        WHISPER_ENDPOINT, data=b"".join(chunks), method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"whisper-1 HTTP {error.code}: {detail[:500]}")
    rows = cloud_word_rows(payload)
    for row in rows:
        row["start"] += lo
        row["end"] += lo
    return rows


def force_align_window(model, audio, ffmpeg, target, lo, hi, temp_dir):
    clip = Path(temp_dir) / f"forced-{lo:.3f}-{hi:.3f}.wav"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{lo:.3f}", "-to", f"{hi:.3f}", "-i", str(audio),
                    "-vn", "-ac", "1", "-ar", "16000", str(clip)], check=True)
    result = model.align(
        str(clip), target, language="en", original_split=True,
        nonspeech_skip=2.0, failure_threshold=0.35,
        suppress_silence=True, verbose=False)
    if result is None:
        return None
    rows = word_rows(result.to_dict())
    for row in rows:
        row["start"] += lo
        row["end"] += lo
    if not rows:
        return None
    probability = sum(row["probability"] for row in rows) / len(rows)
    candidate = best_candidate(rows, target, lo, hi, len(rows), "local-forced-realign")
    if candidate:
        candidate["acoustic_probability"] = probability
        # Exact text is supplied to a forced aligner, so lexical similarity is
        # not evidence. Rank it below a strong independently heard candidate.
        candidate["score"] = 0.72 + min(1.0, probability) * 0.25
        candidate["score"] -= min(
            0.35, max(0.0, candidate["maximum_internal_gap"] - 0.8) * 0.10)
        candidate["score"] -= min(
            0.35, max(0.0, candidate["maximum_word_duration"] - 2.5) * 0.12)
    return candidate


def run(request):
    audio = Path(request["audio"])
    lyrics = request.get("lyrics") or ""
    lines, section_ids = authored_document(lyrics)
    if not audio.is_file() or not lines:
        raise RuntimeError("stable-ts hybrid needs an audio file and authored lyrics")
    model = stable_whisper.load_model(
        request.get("model") or "base.en", device="cpu",
        download_root=request["model_dir"], dq=False)
    full = model.align(
        str(audio), "\n".join(lines), language="en", original_split=True,
        nonspeech_skip=5.0, failure_threshold=0.35,
        suppress_silence=True, verbose=False)
    if full is None:
        raise RuntimeError("stable-ts could not align the authored lyrics")
    segments = full.to_dict().get("segments") or []
    if len(segments) != len(lines):
        raise RuntimeError(f"stable-ts returned {len(segments)} lines for {len(lines)} authored lines")

    qualities = [line_quality(segment) for segment in segments]
    weak = {index for index, (probability, gap) in enumerate(qualities)
            if probability < 0.25 or gap > 2.0 or not (segments[index].get("words") or [])}
    suno = suno_rows(request.get("alignedWords"))
    repairs, warnings = [], []
    cloud_requested = bool(request.get("cloudRepair"))
    cloud_key = os.environ.get("SUNO_STUDIO_OPENAI_KEY", "").strip()
    cloud_failures = 0
    if cloud_requested and not cloud_key:
        warnings.append("hosted whisper-1 repair requested without an OpenAI key; used local fallback")
        cloud_failures += 1
    accepted = [index not in weak for index in range(len(segments))]
    with tempfile.TemporaryDirectory(prefix="suno-stable-ts-") as temp_dir:
        for index in sorted(weak):
            left_anchor = next((j for j in range(index - 1, -1, -1) if accepted[j]), None)
            right_anchor = next((j for j in range(index + 1, len(segments)) if accepted[j]), None)
            lo = max(0.0, float(segments[left_anchor]["end"])) \
                if left_anchor is not None else max(0.0, float(segments[index].get("start") or 0.0) - 2.0)
            hi = float(segments[right_anchor]["start"]) \
                if right_anchor is not None else float(segments[index].get("end") or lo) + 2.0
            authored = segments[index].get("words") or []
            expected = max(1, len(authored) or len(re.findall(r"\w+", lines[index])))
            candidates = []
            suno_candidate = best_candidate(suno, lines[index], lo, hi, expected, "suno")
            if suno_candidate:
                candidates.append(suno_candidate)
            if cloud_requested and cloud_key:
                try:
                    cloud_words = cloud_transcribe_window(
                        cloud_key, audio, request["ffmpeg"], lo, hi, temp_dir)
                    cloud_candidate = best_candidate(
                        cloud_words, lines[index], lo, hi, expected, "cloud-whisper-1")
                    if cloud_candidate:
                        # Independent acoustic evidence should beat forced text
                        # when their lexical/timing scores are otherwise close.
                        cloud_candidate["score"] += 0.08
                        candidates.append(cloud_candidate)
                except Exception as error:
                    cloud_failures += 1
                    warnings.append(f"line {index}: hosted whisper-1 repair failed: {error}")
            try:
                local_words = transcribe_window(
                    model, audio, request["ffmpeg"], lo, hi, temp_dir)
                local_candidate = best_candidate(
                    local_words, lines[index], lo, hi, expected, "local-whisper")
                if local_candidate:
                    candidates.append(local_candidate)
            except Exception as error:
                warnings.append(f"line {index}: local repair transcription failed: {error}")
            try:
                forced_candidate = force_align_window(
                    model, audio, request["ffmpeg"], lines[index], lo, hi, temp_dir)
                if forced_candidate:
                    candidates.append(forced_candidate)
            except Exception as error:
                warnings.append(f"line {index}: bounded forced realignment failed: {error}")
            candidate = max(candidates, key=lambda row: row["score"], default=None)
            forced_ok = bool(candidate and candidate["source"] == "local-forced-realign" and
                             candidate.get("acoustic_probability", 0.0) >= 0.08)
            lexical_ok = bool(candidate and candidate["source"] != "local-forced-realign" and
                              candidate["similarity"] >= 0.55)
            if (candidate and (forced_ok or lexical_ok) and
                    candidate["maximum_internal_gap"] <= 1.5 and
                    candidate["maximum_word_duration"] <= 3.0):
                if not authored:
                    authored = [{"word": word} for word in re.findall(r"\S+", lines[index])]
                # Low proper-name probability alone does not invalidate sane
                # stable-ts timing. A bounded local pass can validate that the
                # line exists while we retain the better full-song timestamps.
                preserve_stable = bool(
                    candidate["source"] == "local-forced-realign" and
                    qualities[index][1] <= 1.5 and authored and
                    max((float(word["end"]) - float(word["start"])
                         for word in authored), default=0.0) <= 3.0)
                if preserve_stable:
                    candidate = dict(candidate)
                    candidate["source"] = "stable-ts-validated-by-local-realign"
                else:
                    segments[index]["words"] = remap(authored, candidate["words"])
                    segments[index]["start"] = segments[index]["words"][0]["start"]
                    segments[index]["end"] = segments[index]["words"][-1]["end"]
                accepted[index] = True
                repairs.append({"line_index": index, "authored_text": lines[index],
                                "matched_audio_text": candidate["text"],
                                "source": candidate["source"],
                                "range": [round(lo, 3), round(hi, 3)],
                                "start": segments[index]["start"],
                                "end": segments[index]["end"],
                                "similarity": round(candidate["similarity"], 4),
                                "acoustic_probability": round(
                                    candidate.get("acoustic_probability", 0.0), 4),
                                "maximum_internal_gap": round(
                                    candidate["maximum_internal_gap"], 3),
                                "maximum_word_duration": round(
                                    candidate["maximum_word_duration"], 3)})
            else:
                summary = [{"source": row["source"],
                            "similarity": round(row["similarity"], 3),
                            "probability": round(row.get("acoustic_probability", 0.0), 3),
                            "gap": round(row["maximum_internal_gap"], 3),
                            "word_duration": round(row["maximum_word_duration"], 3),
                            "text": row["text"]}
                           for row in candidates]
                warnings.append(f"line {index} hidden: no sane local repair; {summary}")

    # Whole-song forced alignment can splice two repeated choruses together.
    # Detect an implausible gap *inside* one authored section, then replace only
    # that section with the section-safe Suno baseline prepared by the app.
    fallback_by_line = {int(row["line_index"]): row
                        for row in request.get("sectionFallbackLines") or []
                        if row.get("words")}
    section_fallbacks = []
    for section_id in sorted(set(section_ids)):
        indexes = [index for index, value in enumerate(section_ids)
                   if value == section_id and accepted[index]]
        internal_gaps = [(left, right,
                          float(segments[right]["start"]) - float(segments[left]["end"]))
                         for left, right in zip(indexes, indexes[1:])]
        worst_gap = max((gap for _, _, gap in internal_gaps), default=0.0)
        section_indexes = [index for index, value in enumerate(section_ids)
                           if value == section_id]
        if worst_gap <= 6.0 or not all(index in fallback_by_line
                                       for index in section_indexes):
            continue
        for index in section_indexes:
            fallback = fallback_by_line[index]
            segments[index]["words"] = [
                {"word": word["word"], "start": float(word["start"]),
                 "end": float(word["end"]), "probability": 0.0}
                for word in fallback["words"]]
            segments[index]["start"] = segments[index]["words"][0]["start"]
            segments[index]["end"] = segments[index]["words"][-1]["end"]
            accepted[index] = True
        repairs = [repair for repair in repairs
                   if repair["line_index"] not in section_indexes]
        section_fallbacks.append({"section_index": section_id,
                                  "line_range": [section_indexes[0], section_indexes[-1]],
                                  "reason": f"{worst_gap:.2f}s internal gap",
                                  "method": "section-safe-suno-fallback"})
        warnings.append(f"section {section_id} used Suno fallback: {worst_gap:.2f}s internal gap")

    aligned = []
    line_diagnostics = []
    for index, (line, segment) in enumerate(zip(lines, segments)):
        probability, gap = qualities[index]
        hidden = not accepted[index]
        line_diagnostics.append({"line_index": index, "text": line,
                                 "start": segment.get("start"), "end": segment.get("end"),
                                 "mean_probability": round(probability, 4),
                                 "maximum_internal_gap": round(gap, 3),
                                 "repaired": any(r["line_index"] == index for r in repairs),
                                 "hidden": hidden})
        if hidden:
            continue
        parenthetical = line.lstrip().startswith("(")
        for word_index, word in enumerate(segment.get("words") or []):
            text = str(word.get("word") or "").strip()
            if not text:
                continue
            aligned.append({"word": ("\n" if word_index == 0 else "") + text,
                            "startS": float(word["start"]), "endS": float(word["end"]),
                            "parenthetical": parenthetical})
    safe_lyrics = "\n".join(line for index, line in enumerate(lines) if accepted[index])
    return {"schema": 1, "signature": request.get("signature"),
            "method": "stable-ts-forced-align+bounded-local-repair",
            "model": request.get("model") or "base.en",
            "cloud_repair_requested": cloud_requested,
            "cloud_repair_model": "whisper-1" if cloud_requested else None,
            "cloud_repairs": sum(repair["source"] == "cloud-whisper-1"
                                 for repair in repairs),
            "retry_cloud": bool(cloud_requested and cloud_failures),
            "alignedWords": aligned, "safe_lyrics": safe_lyrics,
            "rendered_source_lines": sum(accepted),
            "authored_lines": len(lines), "repairs": repairs,
            "section_fallbacks": section_fallbacks,
            "lines": line_diagnostics, "warnings": warnings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    parser.add_argument("output")
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    payload = run(request)
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"lines": payload["rendered_source_lines"],
                      "repairs": len(payload["repairs"]),
                      "warnings": len(payload["warnings"])}))


if __name__ == "__main__":
    main()
