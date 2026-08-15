#!/usr/bin/env python3
"""
Suno Studio - video pipeline doctor.

Diagnoses the ffmpeg side of lyric-video rendering against a real mp3, without
going through the app. Every check is an ACTUAL ffmpeg run, not a guess about
what the build supports.

    python3 video_doctor.py                      # newest mp3 in your output folder
    python3 video_doctor.py "/path/to/song.mp3"  # a specific one

Writes a transcript to video_doctor_report.txt next to this script.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPORT = []


def say(s=""):
    print(s)
    REPORT.append(s)


def rule(t):
    say("")
    say("=" * 74)
    say(f"  {t}")
    say("=" * 74)


def best_error(stderr):
    """The informative line, not just the last one. ffmpeg usually says the
    real cause first and finishes with a generic 'Error opening output files'."""
    lines = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    for pat in ("No such filter", "Unknown filter", "not found", "Cannot load",
                "Unable to", "No option", "Error initializing", "Invalid argument"):
        for l in lines:
            if pat.lower() in l.lower() and "opening output" not in l.lower():
                return l
    return lines[-1] if lines else ""


def run(cmd, timeout=180):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -9, "", f"timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


# --------------------------------------------------------------- find the app
def load_app():
    here = Path(__file__).resolve().parent
    for c in [here / "suno_studio.py",
              here / "Suno Studio.app/Contents/Resources/suno_studio.py",
              Path("/Applications/Suno Studio.app/Contents/Resources/suno_studio.py"),
              Path.home() / "Applications/Suno Studio.app/Contents/Resources/suno_studio.py"]:
        if c.is_file():
            sys.path.insert(0, str(c.parent))
            import importlib
            m = importlib.import_module("suno_studio")
            return m, c
    return None, None


FILTERS = ["drawtext", "subtitles", "gradients", "gblur", "boxblur", "eq",
           "vignette", "blend", "zoompan", "showfreqs", "showwaves",
           "scale", "overlay"]


def ffmpeg_candidates():
    from shutil import which
    out = []
    for p in ["/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
              "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            out.append(p)
    w = which("ffmpeg")
    if w and os.path.realpath(w) not in [os.path.realpath(x) for x in out]:
        out.append(w)
    return out


def build_info(ff):
    rc, so, se = run([ff, "-version"], timeout=30)
    blob = so + se
    ver = blob.splitlines()[0] if blob else "?"
    cfg = ""
    for line in blob.splitlines():
        if line.strip().startswith("configuration:"):
            cfg = line
    libs = [x for x in ["--enable-libass", "--enable-libfreetype",
                        "--enable-libharfbuzz", "--enable-libx264",
                        "--enable-fontconfig"] if x in cfg]
    return ver, libs, cfg


def in_filter_table(ff, name):
    rc, so, se = run([ff, "-hide_banner", "-filters"], timeout=30)
    return bool(re.search(rf"^\s*[A-Z.]{{1,4}}\s+{re.escape(name)}\s", so, re.M))


def help_says_present(ff, name):
    rc, so, se = run([ff, "-hide_banner", "-h", f"filter={name}"], timeout=20)
    blob = so + se
    return bool(blob.strip()) and "Unknown filter" not in blob


def really_works(ff, name, tmp):
    """Actually execute the filter. The only answer that counts."""
    png = tmp / f"_t_{name}.png"
    base = ["-f", "lavfi", "-i", "color=c=0x224466:s=320x180:d=0.2"]
    if name == "drawtext":
        font = find_font()
        if not font:
            return False, "no usable font file found on this Mac"
        vf = f"drawtext=fontfile='{font}':text=ABC:fontcolor=white:fontsize=30"
    elif name == "subtitles":
        ass = tmp / "_t.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,"
            "Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: D,Arial,28,&H00FFFFFF,&H00C8C8C8,&H00000000,&H80000000,0,0,0,0,"
            "100,100,0,0,1,2,1,5,10,10,10,1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
            "Dialogue: 0,0:00:00.00,0:00:05.00,D,,0,0,0,,{\\kf40}hi there\n",
            encoding="utf-8")
        vf = f"subtitles='{str(ass)}'"
    elif name == "gradients":
        base = ["-f", "lavfi", "-i",
                "gradients=s=320x180:c0=0x112233:c1=0x22d3a6:nb_colors=2:duration=1"]
        vf = "null"
    elif name in ("showfreqs", "showwaves"):
        base = ["-f", "lavfi", "-i", "sine=frequency=300:duration=0.3"]
        vf = None
        args = base + ["-filter_complex",
                       f"[0:a]{name}=s=320x100" + (":mode=bar" if name == "showfreqs" else ":mode=cline"),
                       "-frames:v", "1", str(png)]
        rc, so, se = run([ff, "-y", "-hide_banner", "-loglevel", "error"] + args, timeout=60)
        return rc == 0, best_error(se)
    elif name == "zoompan":
        vf = "zoompan=z='min(zoom+0.01,1.2)':d=1:s=320x180"
    elif name == "blend":
        args = ["-f", "lavfi", "-i", "color=c=red:s=320x180:d=0.2",
                "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=0.2",
                "-filter_complex", "[0:v][1:v]blend=all_mode=softlight",
                "-frames:v", "1", str(png)]
        rc, so, se = run([ff, "-y", "-hide_banner", "-loglevel", "error"] + args, timeout=60)
        return rc == 0, best_error(se)
    elif name == "gblur":
        vf = "gblur=sigma=4"
    elif name == "boxblur":
        vf = "boxblur=4:1"
    elif name == "eq":
        vf = "eq=saturation=1.2"
    elif name == "vignette":
        vf = "vignette=angle=PI/4.2"
    elif name == "scale":
        vf = "scale=160:90"
    elif name == "overlay":
        args = ["-f", "lavfi", "-i", "color=c=red:s=320x180:d=0.2",
                "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.2",
                "-filter_complex", "[0:v][1:v]overlay=10:10",
                "-frames:v", "1", str(png)]
        rc, so, se = run([ff, "-y", "-hide_banner", "-loglevel", "error"] + args, timeout=60)
        return rc == 0, best_error(se)
    else:
        vf = "null"
    args = base + ["-vf", vf, "-frames:v", "1", str(png)]
    rc, so, se = run([ff, "-y", "-hide_banner", "-loglevel", "error"] + args, timeout=60)
    return rc == 0, best_error(se)


FONT_HINTS = [
    "/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/SFNSDisplay.ttf",
    "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Geneva.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font():
    return next((p for p in FONT_HINTS if os.path.isfile(p)), None)


def newest_mp3():
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
        if not r.is_dir():
            continue
        for f in r.rglob("*.mp3"):
            t = f.stat().st_mtime
            if t > best_t:
                best, best_t = f, t
    return best


def main():
    tmp = Path(__file__).resolve().parent / "_doctor_tmp"
    tmp.mkdir(exist_ok=True)

    rule("1. ENVIRONMENT")
    say(f"python      {sys.version.split()[0]}")
    say(f"platform    {os.uname().sysname} {os.uname().machine}")
    font = find_font()
    say(f"font        {font or 'NONE FOUND - drawtext cannot work'}")

    cands = ffmpeg_candidates()
    if not cands:
        say("\nffmpeg      NOT FOUND. Install with:  brew install ffmpeg")
        return finish(tmp)
    say(f"\nffmpeg builds found: {len(cands)}")
    for c in cands:
        ver, libs, _ = build_info(c)
        say(f"  {c}")
        try:
            real = os.path.realpath(c)
            kind = "symlink -> " + real if os.path.islink(c) else "real file"
            say(f"      {kind}")
            say(f"      homebrew Cellar build: {'YES' if '/Cellar/' in real else 'NO - not installed by brew'}")
        except Exception:
            pass
        say(f"      {ver}")
        say(f"      relevant flags: {', '.join(libs) if libs else 'NONE of libass/freetype/harfbuzz!'}")
    # where does brew think its ffmpeg is?
    for brew in ("/usr/local/bin/brew", "/opt/homebrew/bin/brew"):
        if os.path.isfile(brew):
            rc, so, se = run([brew, "--prefix", "ffmpeg-full"], timeout=20)
            pref = (so or "").strip()
            say(f"\nbrew --prefix ffmpeg-full: {pref or '(failed) ' + se.strip()[:60]}")
            cb = os.path.join(pref, "bin", "ffmpeg") if pref else ""
            if cb and os.path.isfile(cb):
                v2, l2, _ = build_info(cb)
                say(f"  brew's own binary: {cb}")
                say(f"      {v2}")
                say(f"      relevant flags: {', '.join(l2) if l2 else 'NONE!'}")
                if "--enable-libass" in " ".join(l2):
                    say("  >> This one HAS libass. The app (v2.1+) will prefer it.")
            break

    app, apath = load_app()
    ff = None
    if app:
        try:
            app._FFMPEG_PICK.clear()
            app._FILTER_CACHE.clear()
        except Exception:
            pass
        ff = app.find_ffmpeg()
        say(f"\nsuno_studio.py found at {apath}")
        say(f"the app would use: {ff}")
    else:
        ff = cands[0]
        say(f"\n(suno_studio.py not found next to this script; testing {ff})")

    rule("2. FILTER CAPABILITY  (table / -h / actually ran it)")
    say(f"{'filter':<12} {'-filters':>9} {'-h filter':>10} {'REAL RUN':>9}   note")
    say("-" * 74)
    broken = []
    for name in FILTERS:
        t = in_filter_table(ff, name)
        h = help_says_present(ff, name)
        ok, note = really_works(ff, name, tmp)
        if not ok:
            broken.append(name)
        say(f"{name:<12} {str(t):>9} {str(h):>10} {str(ok):>9}   {note[:28]}")
    say("")
    if "subtitles" in broken:
        say("!! 'subtitles' does not run -> libass missing. No word-level karaoke.")
        say("   Cause: Homebrew's plain `ffmpeg` formula does not depend on libass")
        say("          or freetype. That build genuinely cannot do subtitles/drawtext.")
        say("   Fix:   brew install ffmpeg-full")
        say("          (keg-only, so it lands in <brew prefix>/opt/ffmpeg-full/bin/ffmpeg")
        say("           and will NOT replace `ffmpeg` on your PATH - that's expected;")
        say("           Suno Studio looks there on purpose.)")
    if "drawtext" in broken:
        say("!! 'drawtext' does not run -> freetype missing. No titles either.")
    if not broken:
        say("All filters work. Word-level karaoke is available.")

    rule("3. PIPELINE ON A REAL MP3")
    mp3 = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else newest_mp3()
    if not mp3 or not mp3.is_file():
        say("No mp3 found. Pass one:  python3 video_doctor.py \"/path/to/song.mp3\"")
        return finish(tmp)
    say(f"mp3     {mp3}")
    say(f"size    {mp3.stat().st_size/1e6:.1f} MB")
    words = mp3.with_suffix(".words.json")
    n = 0
    if words.is_file():
        try:
            n = len(json.loads(words.read_text()).get("alignedWords", []))
        except Exception:
            pass
    say(f"timings {'yes, ' + str(n) + ' words' if n else 'NO .words.json - visualiser-only'}")

    if not app:
        say("\nCan't run the real pipeline without suno_studio.py beside this script.")
        return finish(tmp)

    say("\nRunning the app's own render path, stage by stage...")
    app.CONFIG["output_dir"] = str(mp3.parent)
    app.CONFIG["video_height"] = 720          # small + fast for diagnosis
    app.CONFIG["video_crf"] = 28
    app.CONFIG["use_cover_art"] = False
    track = {"file": str(mp3), "name": mp3.name, "song_title": mp3.stem,
             "tags": "", "suno_id": "", "task_id": ""}
    jid = app.start_video_job(track, source="doctor")
    t0 = time.time()
    while time.time() - t0 < 900:
        time.sleep(1)
        j = app.JOBS[jid]
        if j["status"] in ("done", "error"):
            break
    j = app.JOBS[jid]
    say(f"\nresult  {j['status'].upper()}: {j['message']}")
    for tr in j.get("tracks", []):
        p = Path(tr["file"])
        say(f"output  {p}  ({p.stat().st_size/1e6:.1f} MB)")
    if j["status"] == "error":
        say("\nThe app prints full ffmpeg output to:")
        say("  ~/Library/Logs/SunoStudio.log")
    finish(tmp)


def finish(tmp):
    for f in tmp.glob("*"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        tmp.rmdir()
    except OSError:
        pass
    out = Path(__file__).resolve().parent / "video_doctor_report.txt"
    try:
        out.write_text("\n".join(REPORT), encoding="utf-8")
        print(f"\n\nFull report saved to:\n  {out}")
    except Exception as e:
        print(f"(could not write report: {e})")


if __name__ == "__main__":
    main()
