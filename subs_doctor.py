#!/usr/bin/env python3
"""
Suno Studio - subtitle inspector.

Rebuilds the karaoke .ass from a song's cached word timings and reports every
problem it can detect, then renders ONE FRAME per lyric line into a contact
sheet. No video encoding, so a full pass takes seconds instead of minutes.

    python3 subs_doctor.py                       # newest song in your output folder
    python3 subs_doctor.py "/path/to/song.mp3"   # a specific song
    python3 subs_doctor.py song.mp3 --frames 12  # cap the contact sheet
    python3 subs_doctor.py song.mp3 --no-frames  # timeline only, instant
    python3 subs_doctor.py song.mp3 --stable-ts  # local forced-align + repair

Outputs next to this script:
    subs_report.txt     the timeline + warnings
    subs_frames.png     contact sheet (unless --no-frames)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPORT = []


def say(s=""):
    print(s)
    REPORT.append(s)


def load_app():
    here = Path(__file__).resolve().parent
    for c in [here / "suno_studio.py",
              here / "Suno Studio.app/Contents/Resources/suno_studio.py",
              Path("/Applications/Suno Studio.app/Contents/Resources/suno_studio.py"),
              Path.home() / "Applications/Suno Studio.app/Contents/Resources/suno_studio.py"]:
        if c.is_file():
            sys.path.insert(0, str(c.parent))
            import importlib
            return importlib.import_module("suno_studio"), c
    return None, None


def newest_song():
    roots = []
    cfg = Path.home() / ".suno_studio/config.json"
    if cfg.is_file():
        try:
            roots.append(Path(os.path.expanduser(
                json.loads(cfg.read_text()).get("output_dir", ""))))
        except Exception:
            pass
    roots.append(Path.home() / "Music/Suno")
    best, best_t = None, -1
    for r in roots:
        if r.is_dir():
            for f in r.rglob("*.words.json"):
                if f.stat().st_mtime > best_t:
                    best, best_t = f, f.stat().st_mtime
    return best


def recover_lyrics(folder):
    for cand in sorted(folder.glob("*.txt")):
        try:
            body = cand.read_text(encoding="utf-8")
            if "-" * 20 in body:
                return body.split("-" * 20, 1)[1].strip()
        except Exception:
            pass
    return ""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    want_frames = "--no-frames" not in flags
    aligner = "legacy" if "--legacy" in flags else "section"
    want_stable_ts = "--stable-ts" in flags
    cap = 24
    for f in flags:
        if f.startswith("--frames"):
            try:
                cap = int(f.split("=")[1]) if "=" in f else int(sys.argv[sys.argv.index(f) + 1])
            except Exception:
                pass

    app, apath = load_app()
    if not app:
        say("Can't find suno_studio.py next to this script.")
        return finish()

    if args:
        p = Path(args[0]).expanduser()
        words = p if p.name.endswith(".words.json") else p.with_suffix(".words.json")
    else:
        words = newest_song()
    if not words or not words.is_file():
        say("No .words.json found. Pass a song:  python3 subs_doctor.py \"/path/song.mp3\"")
        return finish()

    folder = words.parent
    stem = words.name[:-len(".words.json")]
    say(f"song    {stem}")
    say(f"folder  {folder}")

    data = json.loads(words.read_text())
    aligned = data.get("alignedWords") or []
    lyrics = data.get("lyrics") or recover_lyrics(folder)
    say(f"words   {len(aligned)} timed")
    say(f"lyrics  {'cached with the timings' if data.get('lyrics') else 'recovered from the .txt sidecar' if lyrics else 'MISSING - falling back to guessed line breaks'}")
    if aligned:
        say(f"span    {aligned[0].get('startS', 0):.1f}s to {aligned[-1].get('endS', 0):.1f}s")

    hybrid = None
    if want_stable_ts:
        audio = folder / f"{stem}.mp3"
        if not audio.is_file():
            say(f"stable-ts: audio missing ({audio})")
        elif not lyrics:
            say("stable-ts: authored lyrics are missing")
        else:
            say("stable-ts: running local forced alignment and bounded repair")
            hybrid = app.build_stable_ts_hybrid(
                audio, lyrics, aligned, app.find_ffmpeg(), log=say)
            aligned = hybrid["alignedWords"]

    # ---- does the mapping hold? ----
    alignment = (None if hybrid else
                 app.align_lyrics(aligned, lyrics, method=aligner) if lyrics else None)
    mapped = app.lines_from_lyrics(
        aligned, hybrid.get("safe_lyrics") or "", method="section") if hybrid else \
        alignment.get("groups") if alignment else None
    say(f"\naligner: {hybrid.get('method') if hybrid else alignment.get('method') if alignment else 'audio structure only'}")
    say(f"line breaks: {'from your lyrics' if mapped else 'GUESSED (lyrics did not match the audio)'}")
    groups = mapped or app.group_lyric_lines(aligned)
    if hybrid:
        say(f"stable-ts lines: {hybrid.get('rendered_source_lines', 0)}/"
            f"{hybrid.get('authored_lines', 0)}")
        say(f"bounded repairs: {len(hybrid.get('repairs', []))}")
        for repair in hybrid.get("repairs", []):
            say(f"  line {repair['line_index']}: {repair['source']} "
                f"{repair['start']:.2f}..{repair['end']:.2f} "
                f"{repair['authored_text']!r}")
        for fallback in hybrid.get("section_fallbacks", []):
            say(f"  section {fallback['section_index']}: {fallback['method']} "
                f"({fallback['reason']})")
        for warning in hybrid.get("warnings", []):
            say(f"  warning: {warning}")
    if alignment and alignment.get("overall_confidence") is not None:
        say(f"overall confidence: {alignment['overall_confidence']:.1%}")
        say("sections:")
        for section in alignment.get("sections", []):
            tag = section.get("tag") or f"section {section['index']}"
            unit_range = section.get("audio_unit_range", [])
            say(f"  {tag:<18} {section['confidence']:6.1%}  "
                f"{section['method']:<14} audio units {unit_range}")
            for warning in section.get("warnings", []):
                say(f"    warning: {warning}")
        unmatched = alignment.get("unmatched_audio_words", [])
        skipped = alignment.get("skipped_lyric_text", [])
        say(f"unmatched audio words: {len(unmatched)}"
            + (f"  {unmatched[:12]}" if unmatched else ""))
        say(f"skipped lyric words: {len(skipped)}"
            + (f"  {skipped[:6]}" if skipped else ""))

        weak = [line for line in alignment.get("lines", [])
                if line["confidence"] < 0.55 or line["warnings"] or
                line["unmatched_audio_words"] or line["skipped_lyric_text"]]
        if weak:
            say("diagnostic regions:")
            for line in weak:
                say(f"  {line['section_tag'] or line['section_index']} "
                    f"line {line['line_index']}: {line['confidence']:.1%} "
                    f"{line['method']}  matched={line['matched_text']!r}")
                if line["skipped_lyric_text"]:
                    say(f"    skipped lyric: {line['skipped_lyric_text']}")
                if line["unmatched_audio_words"]:
                    say(f"    unmatched audio: {line['unmatched_audio_words']}")
                for warning in line["warnings"]:
                    say(f"    warning: {warning}")

    # ---- timeline ----
    say("\n" + "=" * 78)
    say("  TIMELINE      (gap = silence before this line)")
    say("=" * 78)
    say(f"  {'#':>3} {'start':>8} {'end':>8} {'dur':>6} {'gap':>6}  text")
    say("  " + "-" * 74)
    events, prev_end = [], 0.0
    warn = []
    for i, rows in enumerate(groups):
        flat = [it for r in rows for it in r]
        s0, e0 = flat[0]["s"], flat[-1]["e"]
        text = app.join_words([x["w"] for r in rows for x in r])
        if len(rows) > 1:
            text = " / ".join(app.join_words([x["w"] for x in r]) for r in rows)
        gap = s0 - prev_end
        events.append((s0, e0, text, len(rows)))
        mark = ""
        if e0 - s0 < 1.0:
            mark += " [HIDDEN]"; warn.append((i, "under 1.0s; hidden by renderer", text))
        if gap > 6:
            mark += " [BIG GAP]"; warn.append((i, f"{gap:.1f}s of silence before it", text))
        if len(rows) > 1:
            mark += " [WRAPPED]"
        if not re.search(r"[A-Za-z0-9]", text):
            mark += " [PUNCT ONLY]"; warn.append((i, "no letters", text))
        say(f"  {i:>3} {s0:8.2f} {e0:8.2f} {e0-s0:6.2f} {gap:6.2f}  {text[:44]!r}{mark}")
        prev_end = e0

    # ---- overlaps (these make libass stack lines) ----
    say("\n" + "=" * 78)
    say("  CHECKS")
    say("=" * 78)
    ov = [(i, i + 1) for i in range(len(events) - 1) if events[i][1] > events[i + 1][0]]
    say(f"  overlapping lines (cause doubled text): {ov if ov else 'none'}")
    say(f"  timed blocks under 1.0s (hidden)      : {sum(1 for e in events if e[1]-e[0] < 1.0)}")
    say(f"  wrapped onto two rows                 : {sum(1 for e in events if e[3] > 1)}")
    longest = max(events, key=lambda e: len(e[2])) if events else None
    if longest:
        say(f"  longest line ({len(longest[2])} chars)              : {longest[2][:50]!r}")
    if warn:
        say("\n  worth a look:")
        for i, why, text in warn[:15]:
            say(f"    line {i:>3}  {why:<32} {text[:34]!r}")

    # ---- frames ----
    if not want_frames:
        return finish()
    ff = app.find_ffmpeg()
    bg = next(iter(sorted(folder.glob("*background.png"))), None)
    if not ff:
        say("\n(no ffmpeg, skipping frames)")
        return finish()

    ass_text, n = app.build_karaoke_ass(aligned, font=app.ass_font_name(),
                                        lyrics_text=(hybrid.get("safe_lyrics") or "") if hybrid else lyrics,
                                        aligner_method=aligner)
    ass = folder / f"{stem}.preview.ass"
    ass.write_text(ass_text, encoding="utf-8")

    tmp = Path(__file__).resolve().parent / "_subs_tmp"
    tmp.mkdir(exist_ok=True)
    if not bg:
        bg = tmp / "bg.png"
        app.make_background(ff, bg, stem, "", cover=None, height=720)
        bg = bg if isinstance(bg, Path) else bg[0]

    step = max(1, len(events) // cap + (1 if len(events) % cap else 0))
    picks = events[::step][:cap]
    say(f"\nrendering {len(picks)} frames (one per lyric line, every {step})...")
    shots = []
    for idx, (s0, e0, text, _) in enumerate(picks):
        mid = s0 + (e0 - s0) * 0.55
        out = tmp / f"f{idx:03d}.png"
        # Output seek so the subtitles filter sees the real timestamp; a low
        # input framerate keeps that cheap (input seek on a looped still image
        # leaves PTS at 0, which renders no subtitle at all).
        r = subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                            "-loop", "1", "-r", "4", "-i", str(bg),
                            "-vf", f"subtitles='{app.ff_path(ass)}',scale=480:270",
                            "-ss", f"{mid:.2f}", "-frames:v", "1", str(out)],
                           capture_output=True, text=True)
        if not out.exists():
            say(f"  frame at {mid:.1f}s failed: {(r.stderr or '').strip().splitlines()[-1:]}")
        if out.exists():
            shots.append((out, f"{s0:.1f}s  {text[:30]}"))
    if shots:
        try:
            from PIL import Image
            cols = 3
            rows_n = -(-len(shots) // cols)
            sheet = Image.new("RGB", (480 * cols, 270 * rows_n), (12, 14, 18))
            for i, (f, _) in enumerate(shots):
                sheet.paste(Image.open(f), ((i % cols) * 480, (i // cols) * 270))
            dest = Path(__file__).resolve().parent / "subs_frames.png"
            sheet.save(dest)
            say(f"contact sheet: {dest}")
            subprocess.run(["open", str(dest)], check=False)
        except ImportError:
            say(f"frames are in {tmp} (install Pillow for a single contact sheet)")
    say(f"preview subtitles: {ass}")
    finish()


def finish():
    out = Path(__file__).resolve().parent / "subs_report.txt"
    try:
        out.write_text("\n".join(REPORT), encoding="utf-8")
        print(f"\nreport: {out}")
    except Exception as e:
        print(f"(could not write report: {e})")


if __name__ == "__main__":
    main()
