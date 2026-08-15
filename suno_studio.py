#!/usr/bin/env python3
"""
Suno Studio - a local web UI for generating songs from your own lyrics.

    python3 suno_studio.py

Opens http://127.0.0.1:8765 in your browser. Paste lyrics, pick a style,
hit Generate. Finished MP3s are downloaded straight to your output folder.

Requires only Python 3.9+ standard library. No pip install.

Config (including your API key) lives in ~/.suno_studio/config.json,
NOT next to this script - so the script is safe to share or commit.
"""

import email
import email.header
import email.utils
import colorsys
import hashlib
import http.server
import imaplib
import json
import mimetypes
import os
import platform
import re
import signal
import socketserver
import ssl
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

APP_VERSION = "4.24"

PORT = 8765
HOST = "127.0.0.1"

CONFIG_DIR = Path.home() / ".suno_studio"
CONFIG_PATH = CONFIG_DIR / "config.json"
SEEN_PATH = CONFIG_DIR / "seen.json"
INBOX_PATH = CONFIG_DIR / "inbox.json"
JOBS_PATH = CONFIG_DIR / "jobs.json"

DEFAULT_CONFIG = {
    "provider": "kie",                  # "kie" | "sunoapi" | "atlascloud"
    "kie_key": "",
    "sunoapi_key": "",
    "atlascloud_key": "",
    "output_dir": str(Path.home() / "Music" / "Suno"),
    "save_lyrics": True,
    # --- Gmail watcher ---
    "watch_enabled": False,
    "gmail_user": "",
    "gmail_app_password": "",           # 16-char app password, NOT your real password
    "gmail_label": "SunoStudio",
    "watch_seconds": 60,
    "default_style": "",
    "max_concurrent": 2,          # external API / encoder slots, not paused jobs
    "suno_single_clip": True,
    # Hands-off mode. Off by default: mail lands in the approval inbox instead.
    # When on, a request only auto-fires if its sender matches allowed_senders.
    "auto_generate": False,
    "allowed_senders": "",              # comma-separated; substring match on the From header
    # --- lyric video (stage 2) ---
    "auto_video": False,                # render a video as soon as a song finishes
    "video_dir": "",                    # blank = beside the mp3; set for a watch folder
    "lyric_y": 0.680,                   # top of lyric text, centered in the lower artwork band
    "lyric_aligner": "section",         # "section" | "stable-ts-hybrid" | "legacy"
    "hybrid_repair": "local",           # "cloud" sends only weak bounded windows
    "copy_path": True,                  # put the finished mp4 path on the clipboard
    # --- running-dry alerts ---
    "alerts_enabled": False,
    "kie_low_credits": 100,             # warn when kie.ai drops below this
    "todoist_token": "",                # optional: file the warning as a task
    "todoist_project": "",              # optional project id; blank = Inbox
    "video_height": 1080,               # 1080 or 720
    "video_fps": 30,
    "visualizer": "bars",               # "bars" | "wave" | "off"
    "shimmer": True,                    # animate bright parts of the background
    "interlude_mode": True,              # stronger gold glints during lyric-free gaps
    "lyric_focus_band": True,            # feathered 90% AI/local band while lyrics sing
    "bg_source": "gradient",            # "gradient" | "ai"
    "openai_key": "",
    "openai_image_model": "gpt-image-2",
    "image_prompt_schema": 1,
    "image_prompt_fragments": {},       # customised English fragments only
    "gate_song": False,
    "gate_image": False,
    "gate_video": False,
    "staging_dir": "",                 # blank = sibling of final output folder
    "rejects_dir": "",                 # blank = sibling of final output folder
    "reject_purge_days": 14,
    "art_title": True,                  # let the artwork carry the title
    "video_crf": 24,                    # lower = better quality, bigger file
}

# Prompt assembly stays in code.  These values are deliberately English-only
# settings so an updated build can refresh untouched fragments safely.
IMAGE_PROMPT_SCHEMA = 1
IMAGE_PROMPT_DEFAULTS = {
    "scene_base": ("Design a striking wide album cover, 16:9 landscape. "
                   "{style}Evoke that genre's era and mood through imagery, colour and texture. "
                   "Rich saturated colour, dramatic light. ABSOLUTELY NO PEOPLE: no humans, faces, "
                   "figures, silhouettes, hands or body parts anywhere. A computer is the centrepiece - "
                   "large, prominent, clearly the focal object, sitting in the LOWER portion of the frame. "
                   "Choose a machine whose design fits the genre's era: a beige CRT terminal, a chunky "
                   "retro home micro, a glowing workstation, a sleek modern laptop, a futuristic holographic "
                   "panel. Angle it so the screen faces the viewer almost straight on and reads clearly. "
                   "COMPOSITION IS CRITICAL. Give the UPPER 55% of the canvas a generous, coherent title "
                   "composition with abundant breathing room. Keep the title entirely above 55% of the frame. "
                   "Continue the rich artwork naturally through the lower frame; do not draw a blank lyric "
                   "bar, panel, rectangle or dividing line. Keep important content clear of the extreme edges."),
    "screen_block": (" THE COMPUTER SCREEN. What follows describes what is drawn INSIDE the screen only. "
                     "All positions in it (upper, below, left, right) refer to the screen's own rectangle, "
                     "never to the album cover. Any colour or background it mentions applies to the screen only "
                     "and must not spread into the surrounding artwork. Text in quotation marks is literal - "
                     "render those words exactly as written, spelled correctly, as small neat labels. Everything "
                     "not in quotation marks is a drawing instruction: draw the shapes described, never write the "
                     "instruction itself. Render it as a clean, crisp, well-designed chart, glowing on the screen "
                     "with the era's appropriate scan lines, phosphor glow or pixel texture. The screen shows: {infographic}"),
    "title_block": (" Integrate the title \"{title}\" as large, beautifully lettered display typography across "
                    "the spacious UPPER 55% - crisp, high contrast, perfectly legible, spelled exactly as given, "
                    "styled to match the era, and set fully inside the frame with a comfortable margin from the top "
                    "and side edges. This title is the only large text in the image; the screen's labels stay small."),
    "tagline_block": " Underneath it, in much smaller neat lettering, the line \"{tagline}\".",
    "no_title_block": " No title and no words in the artwork itself. The only lettering anywhere is the small quoted labels inside the computer screen.",
    "no_title_no_spec_block": " No text, no words, no letters, no typography anywhere.",
    "negatives_with_title": "people, person, human, face, figure, silhouette, hands, blurry, low detail, watermark, signature, cluttered centre, misspelled text, garbled letters, extra words, sentences on screen, wall of text, paragraphs on monitor",
    "negatives_no_title": "people, person, human, face, figure, silhouette, hands, watermark, signature, blurry, cluttered centre, sentences on screen, wall of text",
    "negatives_no_title_no_spec": "people, person, human, face, figure, silhouette, hands, text, words, letters, typography, watermark, signature, blurry, cluttered centre",
}
IMAGE_FRAGMENT_PLACEHOLDERS = {
    "scene_base": {"style"}, "screen_block": {"infographic"},
    "title_block": {"title"}, "tagline_block": {"tagline"},
}

POLL_INTERVAL = 5          # seconds between status checks
POLL_TIMEOUT = 15 * 60     # give up after 15 minutes
IMAP_HOSTS = ("imap.gmail.com", "imap.googlemail.com")
GMAIL_TIMEOUT = 15         # normal Gmail connects take seconds, not minutes

UA = "SunoStudio/1.0 (local)"


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

CONFIG_NEEDS_MIGRATION = False


def load_config():
    global CONFIG_NEEDS_MIGRATION
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception as e:
            print(f"[warn] could not read config: {e}")
    # v4.10 moved lyrics into a lower artwork band to give title lettering far
    # more room. Migrate only shipped defaults; retain genuinely custom values.
    try:
        if any(abs(float(cfg.get("lyric_y")) - old) < 0.0001
               for old in (0.545, 0.490, 0.635)):
            cfg["lyric_y"] = 0.680
    except (TypeError, ValueError):
        cfg["lyric_y"] = 0.680
    # Retired artwork settings were mutually wired in an earlier UI.  Do not
    # preserve dead switches forever, and make old configuration safe to load.
    for stale in ("save_cover", "use_cover_art"):
        if stale in cfg:
            cfg.pop(stale, None)
            CONFIG_NEEDS_MIGRATION = True
    if not isinstance(cfg.get("image_prompt_fragments"), dict):
        cfg["image_prompt_fragments"] = {}
    return cfg


def atomic_write_json(path, value, mode=None):
    """Write JSON beside its destination, fsync it, then replace atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CONFIG_PATH, cfg, mode=0o600)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass


CONFIG = load_config()
if CONFIG_NEEDS_MIGRATION:
    try:
        save_config(CONFIG)
    except Exception as e:
        # A read-only home directory must not prevent the app starting.
        print(f"[warn] could not persist settings migration: {e}")


# --------------------------------------------------------------------------
# tiny http helpers
# --------------------------------------------------------------------------

def _ssl_ctx():
    ctx = ssl.create_default_context()
    return ctx


# The market endpoints proved fussier than the music one: identical JSON that
# curl gets a 200 for came back as "internal error" from the app. The only
# difference was the request headers, so we can swap profiles and remember
# whichever the server actually likes.
HTTP_PROFILES = [
    ("default", {"User-Agent": UA, "Accept": "application/json"}),
    ("curl-like", {"User-Agent": "curl/8.7.1", "Accept": "*/*"}),
    ("bare", {"Accept": "*/*"}),
]
GOOD_PROFILE = {"name": None}
TRANSPORT = {}                       # host -> "urllib" or "curl"
REQUEST_CONTEXT = threading.local()  # worker thread -> pipeline job id
REQUEST_PROCS = {}                    # job id -> active cancellable curl Popen
REQUEST_PROCS_LOCK = threading.Lock()


def request_context_job():
    return getattr(REQUEST_CONTEXT, "job_id", "")


def abort_request(job_id):
    """Terminate only the HTTP child registered by this exact pipeline job."""
    with REQUEST_PROCS_LOCK:
        process = REQUEST_PROCS.get(job_id)
    if process and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _set_request_context(job_id):
    REQUEST_CONTEXT.job_id = job_id


def _clear_request_context():
    REQUEST_CONTEXT.job_id = ""


def curl_json(method, url, api_key, body=None, timeout=60):
    """
    Same request via the curl binary.

    Python's urllib and curl differ in TLS fingerprint and HTTP version, and
    some gateways reject one while accepting the other - which is exactly what
    happened here: identical JSON, 200 from curl, 500 from urllib.

    The key goes in via --config on stdin, never argv, so it can't leak into
    the process list.
    """
    curl = "/usr/bin/curl"
    if not os.path.isfile(curl):
        from shutil import which
        curl = which("curl") or "curl"
    cmd = [curl, "-sS", "--max-time", str(int(timeout)), "-X", method, url,
           "--config", "-"]
    cfg = [f'header = "Authorization: Bearer {api_key}"',
           'header = "Accept: */*"']
    tmp = None
    try:
        if body is not None:
            fd, tmp = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(body, f)
            cfg.append('header = "Content-Type: application/json"')
            cmd += ["--data-binary", f"@{tmp}"]
        job_id = request_context_job()
        event = JOB_CANCELS.get(job_id) if job_id else None
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        if job_id:
            with REQUEST_PROCS_LOCK:
                REQUEST_PROCS[job_id] = p
        try:
            p.stdin.write("\n".join(cfg))
            p.stdin.close()
            p.stdin = None             # communicate() must not flush a closed pipe
            deadline = time.monotonic() + timeout + 15
            while p.poll() is None:
                if event and event.is_set():
                    abort_request(job_id)
                    raise InterruptedError("HTTP request interrupted")
                if time.monotonic() >= deadline:
                    p.kill()
                    raise TimeoutError("curl request timed out")
                time.sleep(0.08)
            stdout, stderr = p.communicate()
        finally:
            if job_id:
                with REQUEST_PROCS_LOCK:
                    if REQUEST_PROCS.get(job_id) is p:
                        REQUEST_PROCS.pop(job_id, None)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if p.returncode != 0:
        raise RuntimeError(f"curl exit {p.returncode}: {(stderr or '').strip()[:200]}")
    try:
        return json.loads(stdout or "")
    except Exception:
        raise RuntimeError(f"non-JSON reply: {(stdout or '')[:200]}")


def api_json(method, url, api_key, body=None, timeout=60, profile=None):
    """Try both transports, remembering preference independently per host."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    preferred = TRANSPORT.get(host)
    # Pipeline workers normally use curl because it can be interrupted. OpenAI
    # image creation is the exception: some captive/hotel networks stall curl
    # while Python's HTTPS stack completes the same request. Try urllib first
    # there, retaining curl as the fallback.
    if request_context_job() and host == "api.openai.com":
        order = ["urllib", "curl"]
    elif request_context_job():
        order = ["curl"]
    else:
        order = ([preferred] + [t for t in ("urllib", "curl") if t != preferred]
                 if preferred else ["urllib", "curl"])
    last = None
    for t in order:
        try:
            res = (http_json(method, url, api_key, body, timeout, profile)
                   if t == "urllib" else
                   curl_json(method, url, api_key, body, timeout))
        except Exception as e:
            last = e
            print(f"[api] {t} transport error: {e}")
            continue
        # A 5xx from one transport but not the other is the tell.
        if res.get("code") in (500, 501) and t == "urllib":
            print(f"[api] urllib got {res.get('code')}; trying curl")
            last = RuntimeError(f"{res.get('code')}: {res.get('msg')}")
            continue
        if TRANSPORT.get(host) != t:
            TRANSPORT[host] = t
            print(f"[api] using the {t} transport for {host}")
        return res
    raise last or RuntimeError("no working transport")


def http_json(method, url, api_key, body=None, timeout=60, profile=None):
    """POST/GET JSON with a bearer token. Returns parsed dict."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    hdrs = dict(HTTP_PROFILES[0][1])
    if profile:
        hdrs = dict(next((h for n, h in HTTP_PROFILES if n == profile), hdrs))
    for k, v in hdrs.items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            raise RuntimeError(f"HTTP {e.code}: {raw[:400]}")
        msg = parsed.get("msg") or parsed.get("message") or parsed.get("error") or raw[:400]
        raise RuntimeError(f"HTTP {e.code}: {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e.reason}")
    try:
        return json.loads(raw)
    except Exception:
        raise RuntimeError(f"non-JSON response: {raw[:400]}")


def download(url, dest: Path, timeout=180, attempts=3, status=None):
    """Download a provider asset with bounded retries for transient reads."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, max(1, attempts) + 1):
        try:
            if status:
                status(f"downloading audio ({attempt}/{max(1, attempts)})")
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            tmp.replace(dest)
            return dest
        except (OSError, TimeoutError, urllib.error.URLError) as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            if attempt >= max(1, attempts):
                raise RuntimeError(f"audio download failed after {attempt} attempts: {e}")
            if status:
                status(f"audio download timed out; retrying ({attempt}/{max(1, attempts)})")
            time.sleep(min(5 * attempt, 15))


def is_transient_network_failure(error):
    """Network hiccups should not discard a provider task that already exists."""
    text = str(error).lower()
    return any(token in text for token in (
        "timed out", "timeout", "network error", "connection reset",
        "connection aborted", "temporarily unavailable", "curl exit"))


def safe_name(s, fallback="Untitled"):
    s = (s or "").strip() or fallback
    s = re.sub(r"[\\/:*?\"<>|\n\r\t]", "-", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:80] or fallback


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suffix, n = p.stem, p.suffix, 2
    while True:
        cand = p.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists():
            return cand
        n += 1


PATH_LOCK = threading.Lock()


def allocate_unique_dir(p: Path) -> Path:
    """Atomically choose and create a unique directory."""
    p.parent.mkdir(parents=True, exist_ok=True)
    stem, n = p.name, 1
    while True:
        candidate = p if n == 1 else p.with_name(f"{stem} ({n})")
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            n += 1


def reserve_unique_path(p: Path) -> Path:
    """Reserve a unique output filename before a concurrent encoder can take it."""
    p.parent.mkdir(parents=True, exist_ok=True)
    with PATH_LOCK:
        candidate = unique_path(p)
        candidate.touch(exist_ok=False)
        return candidate


# --------------------------------------------------------------------------
# provider adapters
# --------------------------------------------------------------------------

class SunoApiOrg:
    """
    sunoapi.org - supports true Custom Mode, where your lyrics are sung
    verbatim. This is the one you want for lyrics-first workflows.
    """
    name = "sunoapi"
    label = "sunoapi.org"
    base = "https://api.sunoapi.org"
    models = ["V5_5", "V5", "V4_5PLUS", "V4_5ALL", "V4_5", "V4"]
    supports_exact_lyrics = True

    def __init__(self, key):
        self.key = key

    def submit(self, f):
        instrumental = bool(f.get("instrumental"))
        has_lyrics = bool((f.get("lyrics") or "").strip())
        # Custom mode = our lyrics/style/title are honoured exactly.
        custom = bool(f.get("title") or f.get("style") or has_lyrics)
        body = {
            "customMode": custom,
            "instrumental": instrumental,
            "model": f.get("model") or "V5",
            # Required by the API but never called back to; we poll instead.
            "callBackUrl": "https://example.com/suno-studio-noop",
        }
        if custom:
            style = (f.get("style") or "").strip() or (CONFIG.get("default_style") or "").strip()
            # Suno requires a style in Custom Mode. Sent blank, it silently
            # falls back to description mode and writes its OWN lyrics
            # instead of singing yours - so refuse rather than waste credits.
            if not style:
                raise RuntimeError(
                    "No style given. Suno needs one in custom mode, or it "
                    "ignores your lyrics and writes its own. Add a style "
                    "(e.g. '70s soul, horn section, 110bpm') or set a "
                    "Default style in Settings.")
            body["style"] = style
            body["title"] = (f.get("title") or "").strip() or "Untitled"
            if not instrumental:
                lyrics = (f.get("lyrics") or "").strip()
                if not lyrics:
                    raise RuntimeError("Custom mode with vocals needs lyrics.")
                body["prompt"] = lyrics
        else:
            body["prompt"] = (f.get("lyrics") or f.get("style") or "").strip()[:500]

        if f.get("negativeTags"):
            body["negativeTags"] = f["negativeTags"]
        if f.get("vocalGender") in ("m", "f"):
            body["vocalGender"] = f["vocalGender"]
        for k in ("styleWeight", "weirdnessConstraint"):
            v = f.get(k)
            if v not in (None, ""):
                try:
                    body[k] = round(float(v), 2)
                except ValueError:
                    pass

        res = http_json("POST", f"{self.base}/api/v1/generate", self.key, body)
        if res.get("code") != 200:
            raise RuntimeError(res.get("msg") or f"provider returned code {res.get('code')}")
        task_id = (res.get("data") or {}).get("taskId")
        if not task_id:
            raise RuntimeError(f"no taskId in response: {json.dumps(res)[:300]}")
        return task_id

    def poll(self, task_id):
        """Returns (state, tracks, message). state: pending|done|error"""
        url = f"{self.base}/api/v1/generate/record-info?taskId={urllib.parse.quote(task_id)}"
        # Poll workers use curl so an in-flight request can be interrupted.
        res = api_json("GET", url, self.key, timeout=45)
        if res.get("code") != 200:
            return "error", [], res.get("msg") or f"code {res.get('code')}"
        data = res.get("data") or {}
        status = data.get("status") or "PENDING"
        if status in ("CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED",
                      "CALLBACK_EXCEPTION", "SENSITIVE_WORD_ERROR"):
            return "error", [], data.get("errorMessage") or status
        suno = ((data.get("response") or {}).get("sunoData")) or []
        tracks = [{
            "title": t.get("title") or "",
            "audio_url": t.get("audioUrl") or t.get("streamAudioUrl") or "",
            "cover_url": t.get("imageUrl") or "",
            "duration": t.get("duration"),
            "tags": t.get("tags") or "",
            "lyrics": t.get("prompt") or "",
            # audioId - required by the timestamped-lyrics and mp4 endpoints
            "suno_id": t.get("id") or "",
        } for t in suno if t.get("audioUrl")]
        if status == "SUCCESS" and tracks:
            return "done", tracks, "complete"
        pretty = {
            "PENDING": "queued at Suno",
            "TEXT_SUCCESS": "lyrics locked, rendering audio",
            "FIRST_SUCCESS": "song ready, provider finalizing the request",
        }.get(status, status)
        return "pending", tracks, pretty

    def timestamps(self, task_id, audio_id):
        """Word-level lyric alignment. Only valid while the track still exists
        on Suno's side (they delete after ~15 days), so we cache it locally."""
        res = http_json("POST", f"{self.base}/api/v1/generate/get-timestamped-lyrics",
                        self.key, {"taskId": task_id, "audioId": audio_id})
        if res.get("code") != 200:
            raise RuntimeError(res.get("msg") or f"code {res.get('code')}")
        data = res.get("data") or {}
        words = [w for w in (data.get("alignedWords") or [])
                 if w.get("startS") is not None and w.get("endS") is not None]
        if not words:
            raise RuntimeError("the provider returned no aligned words for this track")
        return {"alignedWords": words,
                "waveformData": data.get("waveformData") or [],
                "hootCer": data.get("hootCer")}


class KieAi(SunoApiOrg):
    """
    kie.ai speaks the identical API to sunoapi.org - same /api/v1/generate,
    same parameters, same record-info polling, same status enum, same
    get-timestamped-lyrics. Only the host differs, so the whole adapter is
    inherited. Unlike sunoapi.org it sells credit packs rather than a
    subscription, which is why it's the default.
    """
    name = "kie"
    label = "kie.ai"
    base = "https://api.kie.ai"
    supports_exact_lyrics = True


class AtlasCloud:
    """
    Atlas Cloud - aggregator. Its Suno wrapper exposes ONLY {prompt,
    make_instrumental}: there is no custom-lyrics mode, so lyrics get folded
    into the prompt and Suno will paraphrase rather than sing them verbatim.
    """
    name = "atlascloud"
    label = "Atlas Cloud"
    base = "https://api.atlascloud.ai"
    models = ["suno/chirp-v5", "suno/chirp-fenix", "suno/chirp-auk",
              "suno/chirp-v4-tau", "suno/chirp-v4", "suno/chirp-v3-5-tau",
              "suno/chirp-v3-5", "suno/chirp-v3-0"]
    supports_exact_lyrics = False

    def __init__(self, key):
        self.key = key

    def submit(self, f):
        parts = []
        if f.get("style"):
            parts.append(f["style"].strip())
        if f.get("negativeTags"):
            parts.append(f"avoid: {f['negativeTags'].strip()}")
        if f.get("vocalGender") == "m":
            parts.append("male vocal")
        elif f.get("vocalGender") == "f":
            parts.append("female vocal")
        lyrics = (f.get("lyrics") or "").strip()
        if lyrics and not f.get("instrumental"):
            parts.append("Use these exact lyrics:\n" + lyrics)
        prompt = "\n".join(parts).strip()[:2000]
        if not prompt:
            raise RuntimeError("prompt is empty - add a style or lyrics")
        body = {
            "model": f.get("model") or "suno/chirp-v5",
            "prompt": prompt,
            "make_instrumental": bool(f.get("instrumental")),
        }
        res = http_json("POST", f"{self.base}/api/v1/model/generateAudio", self.key, body)
        data = res.get("data") or res
        pid = data.get("id")
        if not pid:
            raise RuntimeError(f"no prediction id: {json.dumps(res)[:300]}")
        return pid

    def poll(self, pid):
        res = http_json("GET", f"{self.base}/api/v1/model/prediction/{urllib.parse.quote(pid)}", self.key)
        data = res.get("data") or res
        status = (data.get("status") or "").lower()
        if status == "failed":
            return "error", [], data.get("error") or "generation failed"
        outs = data.get("outputs") or []
        if status in ("completed", "succeeded") and outs:
            tracks = [{"title": "", "audio_url": u, "cover_url": "",
                       "duration": None, "tags": "", "lyrics": ""} for u in outs]
            return "done", tracks, "complete"
        return "pending", [], status or "processing"


PROVIDERS = {"kie": KieAi, "sunoapi": SunoApiOrg, "atlascloud": AtlasCloud}


def primary_track_only(tracks):
    """One generation request becomes exactly one local song."""
    return list((tracks or [])[:1])


def make_provider(cfg, provider_name=None):
    cls = PROVIDERS.get(provider_name or cfg.get("provider") or "kie", KieAi)
    key = cfg.get(f"{cls.name}_key", "").strip()
    if not key:
        raise RuntimeError(f"No API key set for {cls.label}. Open Settings and paste one in.")
    return cls(key)


# --------------------------------------------------------------------------
# job runner
# --------------------------------------------------------------------------

JOBS = {}
JOB_FORMS = {}
JOBS_LOCK = threading.Lock()

# Only N generations may be in flight at once. Approving a stack of emails
# therefore trickles through instead of firing every request at the provider.
try:
    _slots = threading.Semaphore(max(1, int(CONFIG.get("max_concurrent") or 2)))
except (TypeError, ValueError):
    _slots = threading.Semaphore(2)


def _save_jobs_locked():
    try:
        ordered = sorted(JOBS.values(), key=lambda j: j.get("created", 0))[-200:]
        keep_ids = {j["id"] for j in ordered}
        payload = {"jobs": ordered,
                   "forms": {jid: form for jid, form in JOB_FORMS.items()
                             if jid in keep_ids}}
        atomic_write_json(JOBS_PATH, payload, mode=0o600)
    except Exception as e:
        print(f"[jobs] could not save recovery journal: {e}")


def load_jobs():
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        jobs = data.get("jobs") or []
        forms = data.get("forms") or {}
        return ({j["id"]: j for j in jobs if isinstance(j, dict) and j.get("id")},
                {jid: form for jid, form in forms.items() if isinstance(form, dict)})
    except Exception:
        return {}, {}


JOBS, JOB_FORMS = load_jobs()
JOB_CANCELS = {}                 # runtime only; persisted state remains restart-safe


def final_root():
    return Path(os.path.expanduser(CONFIG.get("output_dir") or str(Path.home() / "Music" / "Suno")))


def final_video_root():
    """Where approved video deliverables land; prefer the configured watch folder."""
    configured = (CONFIG.get("video_dir") or "").strip()
    return Path(os.path.expanduser(configured)) if configured else final_root()


def pipeline_root(kind):
    """Staging/reject defaults deliberately share Final's filesystem."""
    configured = (CONFIG.get(f"{kind}_dir") or "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    return final_root().parent / ("Suno Studio Staging" if kind == "staging" else "Suno Studio Rejects")


def reject_path(job_id, artifact, reason="rejected"):
    src = Path(artifact)
    if not src.exists():
        return None
    target_dir = pipeline_root("rejects") / f"{datetime.now():%Y-%m-%d}" / job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (src.name if src.name not in {"", "."} else reason)
    # reserve_unique_path creates a placeholder file, which is correct for
    # encoders but invalid when the artifact itself is a directory.
    target = unique_path(target)
    try:
        src.replace(target)              # same-volume fast path
    except OSError:
        shutil.move(str(src), str(target))
    return str(target)


def purge_rejects():
    """The only destructive lifecycle rule: aged rejects, never live work."""
    root = pipeline_root("rejects")
    try:
        days = max(1, int(CONFIG.get("reject_purge_days") or 14))
        cutoff = time.time() - days * 86400
        if not root.exists():
            return
        for child in root.iterdir():
            if child.stat().st_mtime < cutoff:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    except Exception as e:
        print(f"[rejects] purge skipped: {e}")


def schedule_reject_purge():
    purge_rejects()
    timer = threading.Timer(24 * 60 * 60, schedule_reject_purge)
    timer.daemon = True
    timer.start()


def set_job(job_id, **kw):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kw)
            _save_jobs_locked()


def job_snapshot(job_id):
    with JOBS_LOCK:
        return dict(JOBS.get(job_id) or {})


def selected_variant(job, key, selected_key):
    selected_id = job.get(selected_key)
    return next((dict(v) for v in job.get(key, []) if v.get("id") == selected_id), None)


def delivery_folder(recipient):
    """Return the final delivery subfolder encoded by the recipient address."""
    recipient = first_email(recipient)
    if not recipient:
        return ""
    return re.sub(r"[^a-z0-9@._+-]", "", recipient)


def move_to_final(source, job_id, recipient=""):
    """Publish a complete video exactly once; no partial Drive-watch events."""
    source = Path(source)
    dest_dir = final_video_root()
    # The delivery watcher uses this final subfolder as routing metadata.  Do
    # not create it during rendering: a gated video must remain private until
    # its recipient has been reviewed and final approval is given.
    if delivery_folder(recipient):
        dest_dir = dest_dir / delivery_folder(recipient)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = reserve_unique_path(dest_dir / source.name)
    try:
        source.replace(dest)             # same-filesystem atomic publication
    except OSError:
        temporary = dest.with_name("." + dest.name + ".publishing")
        shutil.copy2(source, temporary)
        temporary.replace(dest)          # destination observer sees only complete output
        source.unlink()
    return str(dest)


def reject_job_staging(job_id, reason="cancelled"):
    job = job_snapshot(job_id)
    root = Path(job.get("staging_folder") or job.get("folder") or "")
    if root.is_dir():
        return reject_path(job_id, root, reason)
    return None


def cancel_and_remove_job(job_id):
    """Stop a pipeline, preserve its artifacts in Rejects, and forget its card."""
    job = job_snapshot(job_id)
    if not job:
        return None
    JOB_CANCELS.setdefault(job_id, threading.Event()).set()
    abort_request(job_id)
    if job.get("encoder_pid"):
        terminate_interrupted_encoder(
            job.get("encoder_pid"), video_part_path(job.get("output_path") or ""))
    rejected = reject_job_staging(job_id)
    with JOBS_LOCK:
        JOBS.pop(job_id, None)
        JOB_FORMS.pop(job_id, None)
        _save_jobs_locked()
    # Keep the set cancellation event available to any worker that is still
    # unwinding. Removing it here could let setdefault() create a fresh event.
    return rejected


def finalize_pipeline_job(job_id):
    job = job_snapshot(job_id)
    video = job.get("video_path") or job.get("output_path")
    if not video or not Path(video).is_file():
        raise RuntimeError("the completed staging video is missing")
    fields = dict(job.get("current_fields") or JOB_FORMS.get(job_id) or {})
    recipient = first_email(fields.get("recipient") or "")
    published = move_to_final(video, job_id, recipient)
    # Unselected choices remain recoverable; selected intermediate work is
    # ordinary successful staging and is removed with the job folder.
    for key, selected_key in (("song_variants", "selected_song"),
                              ("image_variants", "selected_image")):
        for variant in job.get(key, []):
            if variant.get("id") != job.get(selected_key) and variant.get("file"):
                reject_path(job_id, variant["file"], "unselected")
    root = Path(job.get("staging_folder") or "")
    if root.is_dir():
        try:
            shutil.rmtree(root)
        except OSError as e:
            print(f"[pipeline] could not remove completed staging folder: {e}")
    delivery_note = f" for delivery to {recipient}" if recipient else ""
    set_job(job_id, status="completed", stage="done", message="published to Final" + delivery_note,
            final_path=published, tracks=[{"file": published, "name": Path(published).name, "video": True}])
    with JOBS_LOCK:
        JOB_FORMS.pop(job_id, None)
        _save_jobs_locked()


def run_image_stage(job_id, prompt_override=None):
    """Generate one image variant, then stop or advance according to its gate."""
    acquired = False
    _set_request_context(job_id)
    try:
        if JOB_CANCELS.setdefault(job_id, threading.Event()).is_set():
            return
        job = job_snapshot(job_id)
        song = selected_variant(job, "song_variants", "selected_song")
        if not song:
            raise RuntimeError("select a song variant before generating artwork")
        form = dict(job.get("current_fields") or JOB_FORMS.get(job_id) or {})
        if not _slots.acquire(blocking=False):
            set_job(job_id, status="queued", stage="image", message="waiting for an external-work slot")
            _slots.acquire()
        acquired = True
        set_job(job_id, status="running", stage="image", message="preparing image request")
        root = Path(job.get("staging_folder") or pipeline_root("staging") / job_id)
        folder = root / ("image-" + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True, exist_ok=True)
        image = folder / "art.png"
        prompt = prompt_override or assemble_image_prompt(
            form.get("title") or "Untitled", form.get("style") or "", form.get("infographic") or "",
            bool(CONFIG.get("art_title", True)), form.get("tagline") or "")[0]
        key = (CONFIG.get("openai_key") or "").strip()
        if not key:
            raise RuntimeError("No OpenAI key saved. Settings > OpenAI API key.")
        generate_background_image(key, form.get("title") or "Untitled", form.get("style") or "", image,
                                  draw_title=bool(CONFIG.get("art_title", True)),
                                  tagline=form.get("tagline") or "", infographic=form.get("infographic") or "",
                                  prompt=prompt,
                                  status=lambda message: set_job(job_id, status="running", stage="image",
                                                                 message=message))
        if JOB_CANCELS.setdefault(job_id, threading.Event()).is_set():
            set_job(job_id, status="interrupted", message="image generation interrupted; restart when ready")
            return
        variant = {"id": uuid.uuid4().hex, "file": str(image), "created": time.time(),
                   "prompt": prompt, "inputs": form, "song_variant": song.get("id")}
        job = job_snapshot(job_id)
        variants = list(job.get("image_variants") or []) + [variant]
        paused = bool(CONFIG.get("gate_image"))
        set_job(job_id, image_variants=variants, selected_image=variant["id"],
                image_regenerations=max(0, len(variants) - 1), status=("paused_image" if paused else "running"),
                stage="image", message=("image ready for approval" if paused else "image ready; starting video"))
        if not paused:
            start_pipeline_video(job_id)
    except Exception as e:
        if isinstance(e, InterruptedError) or JOB_CANCELS.setdefault(job_id, threading.Event()).is_set():
            set_job(job_id, status="interrupted", stage="image",
                    message="image generation interrupted; restarting may be billed again")
        else:
            set_job(job_id, status="error", stage="image", message=str(e))
            print(f"[{job_id[:8]}] IMAGE ERROR: {e}")
    finally:
        if acquired:
            _slots.release()
        _clear_request_context()


def start_pipeline_video(job_id):
    job = job_snapshot(job_id)
    song = selected_variant(job, "song_variants", "selected_song")
    image = selected_variant(job, "image_variants", "selected_image")
    if not song or not image:
        set_job(job_id, status="error", message="select song and image variants before video")
        return
    image_file = Path(image.get("file") or "")
    if not image_file.is_file():
        set_job(job_id, status="error", stage="image",
                message="the selected gallery image is missing; select or generate another image")
        return
    track = dict(song.get("track") or {})
    track["file"] = song.get("file") or track.get("file")
    track["pipeline_image"] = str(image_file)
    # Snapshot the exact visible selections onto this same pipeline job.  The
    # renderer must never substitute freshly generated art for a gated image.
    set_job(job_id, status="queued", stage="video", message="queued for video rendering",
            video_song_id=song.get("id"), video_image_id=image.get("id"),
            video_image_file=str(image_file))
    threading.Thread(target=run_video_job, args=(job_id, track), daemon=True).start()


def pipeline_action(job_id, action, fields=None, selected=None, prompt=None):
    """Mutate one paused pipeline job. Backward actions always remain paused."""
    job = job_snapshot(job_id)
    if not job or not job.get("pipeline"):
        raise RuntimeError("that pipeline job no longer exists")
    current = dict(job.get("current_fields") or {})
    if fields:
        current.update({k: v for k, v in fields.items() if k in {
            "title", "tagline", "style", "lyrics", "infographic", "model", "instrumental",
            "negativeTags", "recipient", "vocalGender", "styleWeight", "weirdnessConstraint"}})
        stale = (current.get("lyrics") != (job.get("current_fields") or {}).get("lyrics") or
                 current.get("style") != (job.get("current_fields") or {}).get("style"))
        set_job(job_id, current_fields=current, title=(current.get("title") or "Untitled"),
                style=current.get("style") or "", stale_song=bool(job.get("stale_song") or stale))
        job = job_snapshot(job_id)
    if selected:
        key, select_key = (("song_variants", "selected_song") if selected.get("stage") == "song"
                           else ("image_variants", "selected_image"))
        if any(v.get("id") == selected.get("id") for v in job.get(key, [])):
            set_job(job_id, **{select_key: selected["id"]})
            job = job_snapshot(job_id)
    if action == "save_fields":
        return
    if action == "interrupt":
        if job.get("status") not in ("queued", "submitting", "running"):
            raise RuntimeError("Interrupt is available only while a step is running")
        JOB_CANCELS.setdefault(job_id, threading.Event()).set()
        abort_request(job_id)
        if job.get("encoder_pid"):
            terminate_interrupted_encoder(job.get("encoder_pid"), video_part_path(job.get("output_path") or "x.mp4"))
        set_job(job_id, status="interrupted", message="interrupted; restarting may be billed again")
    elif action == "resubmit_song":
        form = dict(job.get("current_fields") or {})
        JOB_CANCELS[job_id] = threading.Event()
        set_job(job_id, status="queued", stage="song", stale_song=False, task_id="",
                message="resubmitting song")
        JOB_FORMS[job_id] = form
        threading.Thread(target=run_job, args=(job_id, form), daemon=True).start()
    elif action == "retry_song_poll":
        if not job.get("task_id"):
            raise RuntimeError("there is no submitted provider task to resume")
        form = dict(job.get("current_fields") or JOB_FORMS.get(job_id) or {})
        if not form:
            raise RuntimeError("saved song details are missing; cannot safely resume")
        JOB_CANCELS[job_id] = threading.Event()
        set_job(job_id, status="queued", stage="song",
                message="reconnecting to the submitted song task")
        JOB_FORMS[job_id] = form
        threading.Thread(target=run_job, args=(job_id, form), daemon=True).start()
    elif action == "approve_song":
        if job.get("stale_song"):
            raise RuntimeError("lyrics or genre changed; resubmit to Suno before approving")
        threading.Thread(target=run_image_stage, args=(job_id,), daemon=True).start()
    elif action == "regenerate_image":
        JOB_CANCELS[job_id] = threading.Event()
        threading.Thread(target=run_image_stage, args=(job_id, prompt), daemon=True).start()
    elif action == "approve_image":
        if job.get("stale_song"):
            raise RuntimeError("lyrics or genre changed; generate a new song before rendering video")
        JOB_CANCELS[job_id] = threading.Event()
        start_pipeline_video(job_id)
    elif action == "approve_video":
        recipient = first_email(current.get("recipient") or "")
        if (current.get("recipient") or "").strip() and not recipient:
            raise RuntimeError("enter a valid delivery email, or clear the field to publish without delivery routing")
        if recipient != (current.get("recipient") or ""):
            current["recipient"] = recipient
            set_job(job_id, current_fields=current)
        finalize_pipeline_job(job_id)
    elif action in ("back_image", "back_song"):
        if job.get("video_path"):
            reject_path(job_id, job["video_path"], "superseded video")
        set_job(job_id, status=("paused_image" if action == "back_image" else "paused_song"),
                stage=("image" if action == "back_image" else "song"), video_path="",
                message=("returned to image stage" if action == "back_image" else "returned to song stage"))
    elif action == "revert_email":
        original = dict(job.get("original_fields") or {})
        set_job(job_id, current_fields=original, title=original.get("title") or "Untitled",
                style=original.get("style") or "", stale_song=True, message="restored email original; resubmit required")
    elif action == "cancel_remove":
        cancel_and_remove_job(job_id)
    elif action == "cancel":
        JOB_CANCELS.setdefault(job_id, threading.Event()).set()
        abort_request(job_id)
        if job.get("encoder_pid"):
            terminate_interrupted_encoder(job.get("encoder_pid"), video_part_path(job.get("output_path") or ""))
        rejected = reject_job_staging(job_id)
        set_job(job_id, status="cancelled", stage="done", message="cancelled; artifacts moved to rejects",
                rejected_path=rejected or "")
    else:
        raise RuntimeError("unknown pipeline action")


def start_job(form, source="manual"):
    """Register a job and kick off its worker thread. Returns the job id."""
    form = dict(form)
    if not (form.get("style") or "").strip() and (CONFIG.get("default_style") or "").strip():
        form["style"] = CONFIG["default_style"].strip()
    job_id = uuid.uuid4().hex
    provider_name = CONFIG.get("provider") or "kie"
    staging_folder = pipeline_root("staging") / job_id
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "title": (form.get("title") or "").strip() or "Untitled",
            "style": form.get("style", ""),
            "status": "queued",
            "message": "waiting for a free slot" if not _slots._value else "queued",
            "created": time.time(),
            "created_str": datetime.now().strftime("%H:%M"),
            "tracks": [],
            "folder": "",
            "task_id": "",
            "source": source,
            "kind": "generation",
            "provider": provider_name,
            "output_dir": CONFIG.get("output_dir"),
            "original_fields": dict(form), "current_fields": dict(form),
            "song_variants": [], "image_variants": [], "selected_song": None,
            "selected_image": None, "image_regenerations": 0,
            "stale_song": False, "stage": "song",
            "pipeline": True, "staging_folder": str(staging_folder),
        }
        JOB_FORMS[job_id] = dict(form)
        _save_jobs_locked()
    threading.Thread(target=run_job, args=(job_id, form), daemon=True).start()
    return job_id


def run_job(job_id, form):
    def log(msg):
        set_job(job_id, message=msg)
        print(f"[{job_id[:8]}] {msg}")

    acquired = False
    _set_request_context(job_id)
    try:
        if not _slots.acquire(blocking=False):
            log("waiting for a free slot")
            _slots.acquire()
        acquired = True
        with JOBS_LOCK:
            saved_job = dict(JOBS.get(job_id) or {})
        provider_name = saved_job.get("provider") or CONFIG.get("provider") or "kie"
        provider = make_provider(CONFIG, provider_name)
        # Pipeline artifacts never begin life in the watched Final folder.
        out_root = pipeline_root("staging")
        out_root.mkdir(parents=True, exist_ok=True)

        task_id = saved_job.get("task_id") or ""
        if task_id:
            set_job(job_id, status="running")
            log(f"resuming {provider.label} task {task_id[:12]}...")
        else:
            if JOB_CANCELS.setdefault(job_id, threading.Event()).is_set():
                set_job(job_id, status="interrupted", message="song generation interrupted")
                return
            set_job(job_id, status="submitting")
            log("submitting to " + provider.label)
            task_id = provider.submit(form)
            set_job(job_id, task_id=task_id, status="running")
            log("accepted - Suno is writing. This usually takes 1-3 minutes.")

        started = time.time()
        tracks = []
        poll_failures = 0
        while True:
            if JOB_CANCELS.setdefault(job_id, threading.Event()).is_set():
                set_job(job_id, status="interrupted", message="song generation interrupted")
                return
            if time.time() - started > POLL_TIMEOUT:
                raise RuntimeError("timed out waiting for the provider")
            time.sleep(POLL_INTERVAL)
            elapsed = int(time.time() - started)
            try:
                state, tracks, msg = provider.poll(task_id)
                poll_failures = 0
            except Exception as e:
                if not is_transient_network_failure(e):
                    raise
                poll_failures += 1
                if poll_failures >= 6:
                    raise RuntimeError("provider status checks kept timing out; the song task may still "
                                       "finish at the provider. Try resubmitting only after checking "
                                       "your provider dashboard.")
                log(f"provider status check timed out; retrying ({poll_failures}/6, {elapsed}s)")
                continue
            log(f"{msg} ({elapsed}s)")
            if state == "error":
                raise RuntimeError(msg)
            if state == "done":
                break

        # The documented Suno endpoint returns exactly two songs and exposes
        # no output-count field. Keep every returned clip as a selectable
        # variant; `suno_single_clip` is a preference only until a provider
        # offers a supported single-clip request parameter.
        if len(tracks) > 1:
            log(f"provider returned {len(tracks)} clips; keeping all as variants")

        user_title = (form.get("title") or "").strip()
        title = safe_name(user_title or (tracks[0].get("title") if tracks else "") or "Untitled")
        sprint = safe_name((form.get("tagline") or "").strip(), "") if form.get("tagline") else ""
        base = compose_basename(sprint, title)
        stamp = datetime.now().strftime("%Y-%m-%d")
        stage_root = out_root / job_id
        folder = stage_root / ("song-" + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True, exist_ok=True)
        log("downloading one song...")

        saved = []
        for i, t in enumerate(tracks, 1):
            # "<sprint> - <song>"; no "take 1" - the suffix only appears
            # from the second take onward, where it's needed to disambiguate.
            stem = base if user_title else safe_name(t.get("title") or base)
            if i > 1:
                stem = f"{stem} - take {i}"
            mp3 = folder / f"{stem}.mp3"
            download(t["audio_url"], mp3, status=log)
            entry = {
                "file": str(mp3),
                "name": mp3.name,
                "duration": t.get("duration"),
                "tags": t.get("tags"),
                "suno_id": t.get("suno_id", ""),
                "task_id": task_id,
                "song_title": title,
                "style": form.get("style", ""),
                "tagline": form.get("tagline", ""),
                "recipient": form.get("recipient", ""),
                "provider": provider_name,
            }
            # Cache word timings NOW. Suno deletes tracks after ~15 days, and
            # once they're gone the alignment can never be fetched again.
            if not form.get("instrumental") and t.get("suno_id") and hasattr(provider, "timestamps"):
                try:
                    data = provider.timestamps(task_id, t["suno_id"])
                    data["lyrics"] = form.get("lyrics") or ""
                    mp3.with_suffix(".words.json").write_text(json.dumps(data))
                    entry["words"] = len(data["alignedWords"])
                except Exception as e:
                    print(f"  lyric timings unavailable: {e}")
            saved.append(entry)
            log("saved song")

        if CONFIG.get("save_lyrics"):
            words = (form.get("lyrics") or "").strip() or (tracks[0].get("lyrics") if tracks else "")
            meta = [
                f"Title:  {form.get('title') or title}",
                f"Sprint: {form.get('tagline') or ''}",
                f"Style:  {form.get('style') or ''}",
                f"Model:  {form.get('model') or ''}",
                f"Provider: {provider.label}",
                f"Task:   {task_id}",
                f"Date:   {datetime.now():%Y-%m-%d %H:%M}",
                "", "-" * 40, "", words or "(instrumental)",
            ]
            (folder / f"{base}.txt").write_text("\n".join(meta), encoding="utf-8")

        variants = [{"id": uuid.uuid4().hex, "file": x["file"], "created": time.time(),
                     "inputs": dict(form), "track": x} for x in saved]
        # A new song request is another option, never a replacement for an
        # already-approved take.  Keep all prior audio selectors recoverable.
        existing = job_snapshot(job_id)
        all_tracks = list(existing.get("tracks") or []) + saved
        all_variants = list(existing.get("song_variants") or []) + variants
        set_job(job_id, status=("paused_song" if CONFIG.get("gate_song") else "done"),
                message=("song ready for approval" if CONFIG.get("gate_song") else
                         f"song ready - {len(saved)} variant(s)"),
                tracks=all_tracks, song_variants=all_variants,
                selected_song=(variants[0]["id"] if variants else existing.get("selected_song")),
                folder=str(stage_root), staging_folder=str(stage_root), stage="song")
        print(f"[{job_id[:8]}] finished -> {folder}")

        if saved and not CONFIG.get("gate_song"):
            log("starting image stage")
            threading.Thread(target=run_image_stage, args=(job_id,), daemon=True).start()

    except Exception as e:
        if isinstance(e, InterruptedError) or JOB_CANCELS.setdefault(job_id, threading.Event()).is_set():
            set_job(job_id, status="interrupted", stage="song",
                    message="song generation interrupted; restarting may be billed again")
        else:
            set_job(job_id, status="error", message=str(e))
            print(f"[{job_id[:8]}] ERROR: {e}")
    finally:
        if acquired:
            _slots.release()
        _clear_request_context()


def resume_persisted_jobs():
    """Resume safe generation states without ever double-submitting a task."""
    generations, videos = [], []
    with JOBS_LOCK:
        for job_id, job in JOBS.items():
            status = job.get("status")
            if job.get("pipeline"):
                stage = job.get("stage") or "song"
                if status in ("paused_song", "paused_image", "paused_video", "completed", "cancelled", "error"):
                    continue
                if stage == "song":
                    form = job.get("current_fields") or JOB_FORMS.get(job_id)
                    if form:
                        generations.append((job_id, dict(form)))
                    else:
                        job.update(status="error", message="cannot resume: missing song fields")
                elif stage == "image":
                    # Never bill a duplicate image after an app restart. The
                    # operator can explicitly regenerate from this safe pause.
                    job.update(status="paused_image", message="image work interrupted; regenerate when ready")
                elif stage == "video":
                    song = selected_variant(job, "song_variants", "selected_song")
                    image = selected_variant(job, "image_variants", "selected_image")
                    if song and image:
                        track = dict(song.get("track") or {})
                        track["file"] = song.get("file") or track.get("file")
                        track["pipeline_image"] = image.get("file")
                        job["video_song_id"] = song.get("id")
                        job["video_image_id"] = image.get("id")
                        job["video_image_file"] = image.get("file")
                        videos.append((job_id, track))
                    else:
                        job.update(status="error", message="cannot resume: missing selected variant")
                continue
            if job.get("kind") == "video" and status in ("queued", "running"):
                form = JOB_FORMS.get(job_id) or {}
                track = form.get("track")
                if isinstance(track, dict) and track.get("file"):
                    job.update(status="queued", message="recovering interrupted video")
                    videos.append((job_id, dict(track)))
                else:
                    job.update(status="error", message=(
                        "cannot resume video: saved input details are missing"))
            elif status == "submitting" and not job.get("task_id"):
                job.update(status="error", message=(
                    "app restarted while submitting; not retried automatically to avoid duplicate credits"))
            elif job.get("kind", "generation") == "generation" and status in ("queued", "running"):
                form = JOB_FORMS.get(job_id)
                if form:
                    generations.append((job_id, dict(form)))
                else:
                    job.update(status="error", message="cannot resume: saved request details are missing")
        _save_jobs_locked()
    for job_id, form in generations:
        threading.Thread(target=run_job, args=(job_id, form), daemon=True).start()
    for job_id, track in videos:
        threading.Thread(target=run_video_job, args=(job_id, track, True),
                         daemon=True).start()


# --------------------------------------------------------------------------
# lyric video rendering (ffmpeg)
# --------------------------------------------------------------------------

# Homebrew split ffmpeg in two: the plain `ffmpeg` formula has NO libass or
# freetype, so no subtitles and no drawtext. `ffmpeg-full` has them, but it is
# keg-only - brew never symlinks it into bin - hence the opt/ paths first.
FFMPEG_HINTS = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
]

# Paths without spaces or colons first - keeps drawtext escaping simple.
FONT_HINTS = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Geneva.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

# All three stops are mid-tone or brighter. A dark stop used to swallow the
# lower two thirds of the frame - exactly where the lyrics sit - leaving
# something close to text on black. Contrast comes from the vignette and a
# slight brightness pull instead.
PALETTES = [
    ("0x3d2a8c", "0x00d0a4", "0x7c5cff"),   # violet / mint
    ("0x8c2452", "0xff5d6c", "0xffb020"),   # crimson / amber
    ("0x1c6b7a", "0x22d3a6", "0x4cc9f0"),   # teal / sky
    ("0x6b1f5c", "0xc21f6e", "0xf7b733"),   # magenta / gold
    ("0x2a4a8c", "0x7c5cff", "0x4cc9f0"),   # indigo / cyan
    ("0x8c4a1f", "0xff8c42", "0xffd166"),   # rust / sun
    ("0x2f6b4a", "0x52b788", "0xa8dab5"),   # forest / sage
    ("0x6b2a7a", "0x9d4edd", "0xff9ff3"),   # orchid / blush
]


_FFMPEG_PICK = []


def find_ffmpeg():
    """Pick the most capable ffmpeg on the machine, not merely the first.
    Intel Macs put Homebrew in /usr/local, Apple Silicon in /opt/homebrew, and
    a stray minimal build elsewhere on PATH shouldn't win."""
    if _FFMPEG_PICK:
        return _FFMPEG_PICK[0] or None
    from shutil import which
    cands = [p for p in FFMPEG_HINTS if os.path.isfile(p) and os.access(p, os.X_OK)]
    w = which("ffmpeg")
    if w and os.path.realpath(w) not in [os.path.realpath(c) for c in cands]:
        cands.append(w)
    # Ask Homebrew where its own ffmpeg lives. A hand-installed minimal build
    # in /usr/local/bin will shadow it on PATH, and that stripped binary often
    # lacks libass/freetype - so go straight to the Cellar copy too.
    for brew in ("/usr/local/bin/brew", "/opt/homebrew/bin/brew"):
        if not os.path.isfile(brew):
            continue
        for formula in ("ffmpeg-full", "ffmpeg"):
            try:
                r = subprocess.run([brew, "--prefix", formula],
                                   capture_output=True, text=True, timeout=15)
                cellar = os.path.join((r.stdout or "").strip(), "bin", "ffmpeg")
                if os.path.isfile(cellar) and os.access(cellar, os.X_OK) and \
                   os.path.realpath(cellar) not in [os.path.realpath(c) for c in cands]:
                    cands.append(cellar)
            except Exception:
                pass
        break
    if not cands:
        _FFMPEG_PICK.append("")
        return None
    if len(cands) > 1:
        def score(c):
            f = ffmpeg_filters(c)
            return (("subtitles" in f) * 1000) + (("drawtext" in f) * 500) + len(f)
        cands.sort(key=score, reverse=True)
        print(f"[ffmpeg] {len(cands)} builds found; using {cands[0]}")
    _FFMPEG_PICK.append(cands[0])
    return cands[0]


def find_font():
    return next((p for p in FONT_HINTS if os.path.isfile(p)), None)


def audio_duration(ff, path):
    """Seconds, via ffprobe. The render needs an explicit -t: with a looped
    still image as the video source, -shortest alone does not stop the encode."""
    probe = str(Path(ff).with_name("ffprobe"))
    if not os.path.isfile(probe):
        from shutil import which
        probe = which("ffprobe") or ""
    if not probe:
        return None
    try:
        p = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True)
        return float((p.stdout or "").strip())
    except (ValueError, OSError):
        return None


def valid_image(ff, path):
    """True only for a decodable image, never a half-written API response."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    try:
        width, height = image_dimensions(ff, path)
        return width > 0 and height > 0
    except Exception:
        return False


def completed_video_matches(ff, path, expected_duration):
    """Recognize a completed encode after a crash between rename and journaling."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 100_000:
        return False
    actual = audio_duration(ff, path)
    if not actual or not expected_duration:
        return False
    return actual >= max(0.0, expected_duration - 1.0)


def video_part_path(path):
    path = Path(path)
    return path.with_suffix(path.suffix + ".part")


def terminate_interrupted_encoder(pid, expected_output):
    """Stop only the verified FFmpeg child left behind by this video job."""
    try:
        pid = int(pid)
        if pid <= 1:
            return False
        probe = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5)
        command = (probe.stdout or "").strip()
        if probe.returncode != 0 or "ffmpeg" not in command.lower():
            return False
        if str(expected_output) not in command:
            return False
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.1)
        os.kill(pid, signal.SIGKILL)
        return True
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        return False


def cleanup_video_scratch(folder, job_id):
    """Remove only stale scratch directories belonging to this exact job."""
    prefix = f".suno-{str(job_id)[:8]}-"
    for candidate in Path(folder).glob(prefix + "*"):
        if not candidate.is_dir() or not candidate.name.startswith(prefix):
            continue
        try:
            for child in candidate.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink()
            candidate.rmdir()
        except OSError:
            pass


def ass_font_name():
    """libass resolves by family name via CoreText/fontconfig."""
    f = find_font() or ""
    if "Helvetica" in f:
        return "Helvetica Neue" if "Neue" in f else "Helvetica"
    if "SFNS" in f:
        return "Helvetica Neue"
    if "Arial" in f:
        return "Arial"
    if "DejaVu" in f:
        return "DejaVu Sans"
    return "Helvetica"


_FILTER_CACHE = {}

# Filters we'd like. Only the first two are non-negotiable; the rest degrade.
CORE_FILTERS = ["scale", "overlay"]
NICE_FILTERS = ["gradients", "gblur", "boxblur", "eq", "vignette", "blend",
                "drawbox", "lutyuv", "noise", "unsharp", "drawtext", "subtitles",
                "showfreqs", "showwaves"]


def ffmpeg_filters(ff):
    """Set of filter names this ffmpeg build actually ships. Minimal builds
    (and some static ones) omit drawtext/subtitles because those need
    libfreetype and libass."""
    if ff in _FILTER_CACHE:
        return _FILTER_CACHE[ff]
    names = set()
    try:
        p = subprocess.run([ff, "-hide_banner", "-filters"],
                           capture_output=True, text=True, timeout=20)
        for line in (p.stdout or "").splitlines():
            m = re.match(r"^\s*[A-Z.]{1,4}\s+(\S+)\s+", line)
            if m:
                names.add(m.group(1))
    except Exception as e:
        print(f"[ffmpeg] could not list filters: {e}")

    # The -filters table format has shifted between releases, so a regex miss
    # is not proof of absence. Ask ffmpeg about anything we didn't find -
    # `-h filter=X` is authoritative and version-proof.
    for want in CORE_FILTERS + NICE_FILTERS:
        if want in names:
            continue
        try:
            q = subprocess.run([ff, "-hide_banner", "-h", f"filter={want}"],
                               capture_output=True, text=True, timeout=15)
            blob = (q.stdout or "") + (q.stderr or "")
            if blob.strip() and "Unknown filter" not in blob and "not found" not in blob.lower():
                names.add(want)
                print(f"[ffmpeg] '{want}' missed by the filter table but is present")
        except Exception:
            pass
    _FILTER_CACHE[ff] = names
    return names


def missing_filters(ff):
    have = ffmpeg_filters(ff)
    if not have:
        return []          # probe failed; don't cry wolf
    return [f for f in CORE_FILTERS + NICE_FILTERS if f not in have]


def ffmpeg_progress_seconds(value):
    """Parse FFmpeg's machine-readable HH:MM:SS.microseconds timestamp."""
    try:
        hour, minute, second = str(value).strip().split(":", 2)
        return int(hour) * 3600 + int(minute) * 60 + float(second)
    except (TypeError, ValueError):
        return None


def run_ffmpeg(ff, args, what="ffmpeg", progress_callback=None, duration=None,
               process_callback=None):
    command = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    if progress_callback and duration and duration > 0:
        # Keep stderr out of the pipe we consume so a verbose encoder failure
        # cannot deadlock while progress is being read.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            p = subprocess.Popen(
                command + ["-progress", "pipe:1", "-nostats"] + args,
                stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1)
            if process_callback:
                process_callback(p.pid)
            for raw in p.stdout or ():
                key, separator, value = raw.strip().partition("=")
                if separator and key == "out_time":
                    seconds = ffmpeg_progress_seconds(value)
                    if seconds is not None:
                        try:
                            progress_callback(max(0.0, min(1.0, seconds / duration)))
                        except Exception as error:
                            print(f"[ffmpeg] progress callback failed: {error}")
            if p.stdout:
                p.stdout.close()
            returncode = p.wait()
            errors.seek(0)
            stderr = errors.read()
        p = subprocess.CompletedProcess(command + args, returncode, "", stderr)
        if p.returncode == 0:
            try:
                progress_callback(1.0)
            except Exception as error:
                print(f"[ffmpeg] progress callback failed: {error}")
    else:
        p = subprocess.run(command + args, capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        # Full context to the log file - the UI only has room for one line.
        print(f"[ffmpeg] {what} failed using {ff}")
        print(f"[ffmpeg] args: {' '.join(args)}")
        for line in err.splitlines():
            print(f"[ffmpeg] {line}")
        if "Filter not found" in err or "No such filter" in err:
            gone = [f for f in NICE_FILTERS if f not in ffmpeg_filters(ff)]
            extra = (f" Your ffmpeg is missing: {', '.join(gone)}." if gone else "")
            raise RuntimeError(
                f"{what} failed - your ffmpeg build is missing a filter.{extra}"
                " Install the full build:  brew install ffmpeg-full")
        tail = err.splitlines()
        raise RuntimeError(f"{what} failed: " + (tail[-1] if tail else f"exit {p.returncode}")[:300])
    return p


def ass_time(t):
    t = max(0.0, float(t))
    return f"{int(t//3600):d}:{int(t%3600//60):02d}:{t%60:05.2f}"


OPENERS = ("(", "[", "{", "\u201c", '"', "\u2018")
CLOSERS = (")", "]", "}", "\u201d", "\u2019")


def needs_space(prev, cur):
    """Suno splits contractions across entries ("We'" + "re"). Joining those
    with a space produces "We' re"."""
    if not prev:
        return False
    # A straight quote is both an opener and a closer, so decide by parity:
    # an odd count means the last one opened a quote (no space after it),
    # an even count means it closed one (space needed).
    if prev.endswith('"'):
        return prev.count('"') % 2 == 0
    # ...and a quote that closes an open one hugs the word before it
    if cur.startswith('"') and prev.count('"') % 2 == 1:
        return False
    if prev.endswith(("'", "\u2019", "-", "\u2011", "\u2013", "(", "[", "{",
                      "\u201c", "\u2018")):
        return False
    if cur.startswith((",", ".", "!", "?", ":", ";") + CLOSERS):
        return False
    return True


def join_words(words):
    out = ""
    for w in words:
        out += (" " if needs_space(out, w) else "") + w
    return out


def _clean_items(aligned):
    items = []
    for w in aligned:
        raw = w.get("word") or ""
        clean = re.sub(r"\[[^\]]+\]", " ", raw)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            continue
        try:
            it = {"w": clean, "s": float(w["startS"]), "e": float(w["endS"])}
        except (KeyError, TypeError, ValueError):
            continue
        if it["e"] < it["s"]:
            it["e"] = it["s"] + 0.2
        items.append(it)
    return items


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def unorphan(chunk):
    """Glue a lone opening bracket onto the word after it, so the karaoke fill
    treats "(Ooh" as one unit rather than two."""
    out = []
    for it in chunk or []:
        if out and out[-1]["w"] in OPENERS:
            prev = out.pop()
            merged = dict(it)
            merged.update({"w": prev["w"] + it["w"],
                           "s": prev["s"], "e": it["e"]})
            if prev.get("parenthetical") or it.get("parenthetical"):
                merged["parenthetical"] = True
            it = merged
        out.append(it)
    return out


def legacy_lines_from_lyrics(aligned, lyrics_text, max_chars=46):
    """
    Split the timed words at the line breaks the EMAIL used.

    Uses difflib to align the sung words against the submitted lyrics. That
    handles ad-libs, dropped words and contraction splits for free - the
    hand-rolled prefix matcher this replaces desynced on the first surprise
    and dragged every later line with it.
    """
    import difflib

    items = _clean_items(aligned)
    if not items or not (lyrics_text or "").strip():
        return None

    # flatten the authored lyrics into (line number, normalised token)
    authored = []
    for li, raw_line in enumerate(lyrics_text.split("\n")):
        line = re.sub(r"\[[^\]]+\]", " ", raw_line)
        for t in re.split(r"\s+", line):
            n = _norm(t)
            if n:
                authored.append((li, n))
    if not authored:
        return None

    # Align on CHARACTERS, not words. Suno splits contractions ("we'" + "re")
    # where the lyrics have one token, so token-level matching drops them.
    sung_chars, sung_owner = [], []
    for i, it in enumerate(items):
        for ch in _norm(it["w"]):
            sung_chars.append(ch)
            sung_owner.append(i)
    auth_chars, auth_line = [], []
    for li, tok in authored:
        for ch in tok:
            auth_chars.append(ch)
            auth_line.append(li)
    if not sung_chars or not auth_chars:
        return None

    sm = difflib.SequenceMatcher(None, sung_chars, auth_chars, autojunk=False)
    votes = [{} for _ in items]
    matched = 0
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            owner = sung_owner[a + k]
            line = auth_line[b + k]
            votes[owner][line] = votes[owner].get(line, 0) + 1
        matched += size

    if matched < 0.4 * len(sung_chars):
        return None            # the audio isn't singing these lyrics

    line_of = [max(v, key=v.get) if v else None for v in votes]
    first = next((v for v in line_of if v is not None), 0)
    last = first
    for i in range(len(items)):
        if line_of[i] is None:
            line_of[i] = last
        else:
            last = line_of[i]

    groups, cur, cur_line = [], [], None
    for i, it in enumerate(items):
        if cur and line_of[i] != cur_line:
            groups.append([unorphan(cur)])
            cur = []
        cur_line = line_of[i]
        cur.append(it)
    if cur:
        groups.append([unorphan(cur)])
    return groups or None


SECTION_TAG_RE = re.compile(
    r"^\s*\[(verse|chorus|pre[- ]?chorus|bridge|outro|intro|hook|refrain|"
    r"post[- ]?chorus|interlude|break|instrumental)(?:\s+[^\]]*)?\]\s*$",
    re.IGNORECASE,
)


def _section_kind(tag):
    """Return a comparable kind while retaining the original authored tag."""
    m = SECTION_TAG_RE.match(tag or "")
    return re.sub(r"[- ]", "", m.group(1).lower()) if m else ""


def parse_authored_lyrics(lyrics_text):
    """Parse authored lyrics into ordered tagged sections and non-empty lines."""
    sections, current = [], None

    def start(tag=""):
        section = {"index": len(sections), "tag": tag.strip(),
                   "kind": _section_kind(tag), "lines": []}
        sections.append(section)
        return section

    for raw in (lyrics_text or "").splitlines():
        text = raw.strip()
        if SECTION_TAG_RE.match(text):
            current = start(text)
        elif text:
            if current is None:
                current = start()
            current["lines"].append({"index": len(current["lines"]),
                                     "text": text})
    return [s for s in sections if s["lines"]]


def _timed_items(aligned):
    """Clean timed words while retaining Suno's structural hints."""
    items = []

    def plausible_duration(text):
        # Used only for obvious structure-boundary outliers. Long names need
        # more time than short words, but no single token should inherit four
        # or sixteen seconds of an instrumental gap from a section marker.
        return max(0.65, min(1.50, 0.25 + 0.11 * len(_chars(text))))

    for source_index, entry in enumerate(aligned or []):
        raw = entry.get("word") or ""
        tags = re.findall(r"\[[^\]]+\]", raw)
        without_tags = re.sub(r"\[[^\]]+\]", "", raw)
        clean = re.sub(r"\s+", " ", without_tags).strip()
        if not clean:
            continue
        try:
            start, end = float(entry["startS"]), float(entry["endS"])
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            end = start + 0.2
        # Suno occasionally combines the prior word and the next line's lone
        # opener in one entry: "fine\n\n(". Split only that structural opener;
        # the zero-length boundary item is later glued to the response word.
        split_opener = re.match(r"^(.*?)\s*[\r\n]+\s*([\(\[\{])\s*$",
                                without_tags, re.DOTALL)
        if split_opener and split_opener.group(1).strip():
            first = re.sub(r"\s+", " ", split_opener.group(1)).strip()
            first_end = end
            if "\n\n" in without_tags and end - start > 3.0:
                first_end = min(end, start + plausible_duration(first))
            first_item = {"w": first, "s": start, "e": first_end, "raw": raw,
                          "source_index": source_index, "tags": tags,
                          "newline_before": False, "newline_after": True}
            if first_end < end:
                first_item["timing_warning"] = \
                    "trimmed structure-boundary silence after final word"
            items.append(first_item)
            # The opener belongs at Suno's original boundary timestamp even
            # when the preceding held word had an inflated end time.
            items.append({"w": split_opener.group(2), "s": end, "e": end,
                          "raw": split_opener.group(2),
                          "source_index": source_index, "tags": [],
                          "newline_before": True, "newline_after": False})
            continue
        # Suno also glues an inline response opener to the preceding word
        # ("path (" / "screen ("). Keep the opener as its own timed item so
        # it can inherit the parenthetical colour from the following word.
        inline_opener = re.match(r"^(.*?)\s*([\(\[\{])\s*$", without_tags,
                                 re.DOTALL)
        if inline_opener and inline_opener.group(1).strip():
            first = re.sub(r"\s+", " ", inline_opener.group(1)).strip()
            items.append({"w": first, "s": start, "e": end, "raw": raw,
                          "source_index": source_index, "tags": tags,
                          "newline_before": False, "newline_after": False})
            items.append({"w": inline_opener.group(2), "s": end, "e": end,
                          "raw": inline_opener.group(2),
                          "source_index": source_index, "tags": [],
                          "newline_before": False, "newline_after": False})
            continue
        first_text = next((i for i, ch in enumerate(without_tags)
                           if not ch.isspace()), len(without_tags))
        last_text = max((i for i, ch in enumerate(without_tags)
                         if not ch.isspace()), default=-1)
        newline_before = "\n" in without_tags[:first_text]
        newline_after = "\n" in without_tags[last_text + 1:]
        # A newline embedded in a non-empty token is still a useful boundary.
        if "\n" in without_tags and not (newline_before or newline_after):
            newline_after = True
        original_start = start
        if (tags or newline_before) and end - start > 2.0:
            start = max(start, end - plausible_duration(clean))
        item = {"w": clean, "s": start, "e": end, "raw": raw,
                "source_index": source_index, "tags": tags,
                "newline_before": newline_before,
                "newline_after": newline_after}
        if start > original_start:
            item["timing_warning"] = \
                "trimmed structure-boundary silence before first word"
        items.append(item)
    return items


def segment_audio_chunks(aligned, line_gap=1.1, section_gap=3.0):
    """Create chronological audio units from tags, newlines and timestamp gaps.

    Units normally correspond to Suno lines. Section labels and long gaps are
    retained as candidate section boundaries for the monotonic section DP.
    """
    items = _timed_items(aligned)
    units, current, previous_break = [], [], False
    current_gap, current_boundary = 0.0, False
    for item in items:
        gap = item["s"] - current[-1]["e"] if current else 0.0
        tag_boundary = bool(item["tags"])
        forced = bool(current and (previous_break or item["newline_before"] or
                                   tag_boundary or gap > line_gap))
        if forced:
            units.append({"items": current, "section_boundary": current_boundary,
                          "gap_before": current_gap,
                          "tags": current[0].get("tags", [])})
            current = []
            current_gap = gap
            current_boundary = tag_boundary or gap > section_gap
        elif not current:
            current_boundary = tag_boundary
        current.append(item)
        previous_break = item["newline_after"]
    if current:
        units.append({"items": current, "section_boundary": current_boundary,
                      "gap_before": current_gap,
                      "tags": current[0].get("tags", [])})
    # A tag belongs to the unit it starts, not the preceding unit.
    for i, unit in enumerate(units):
        unit["index"] = i
        unit["tags"] = [t for it in unit["items"] for t in it.get("tags", [])]
        if unit["tags"]:
            unit["section_boundary"] = True
    # When Suno gives a final word an enormous duration ending in an opener,
    # it can put the following response on the wrong side of an instrumental
    # break. Repair only the strongly constrained pattern: the preceding word
    # was already identified as a structure-boundary outlier, the current unit
    # is parenthetical, and the next section begins almost immediately after it.
    for i in range(1, len(units) - 1):
        previous, current, following = units[i - 1], units[i], units[i + 1]
        previous_last = previous["items"][-1]
        current_text = join_words([it["w"] for it in current["items"]]).strip()
        gap_before = current["items"][0]["s"] - previous_last["e"]
        gap_after = following["items"][0]["s"] - current["items"][-1]["e"]
        if (previous_last.get("timing_warning") ==
                "trimmed structure-boundary silence after final word" and
                current_text.startswith("(") and gap_before > 4.0 and
                gap_after < 0.8):
            # The giant boundary token proves the response belongs before the
            # instrumental break, but not that it starts immediately. Place it
            # in the early part of the ambiguous window (about 1:45 in the Med
            # fixture) instead of snapping it against the preceding line.
            response_delay = min(3.0, max(0.35, gap_before * 0.18))
            target = previous_last["e"] + response_delay
            shift = target - current["items"][0]["s"]
            for item in current["items"]:
                item["s"] += shift
                item["e"] += shift
                item["timing_warning"] = \
                    "moved response before structure-boundary instrumental gap"
            current["gap_before"] = response_delay
            following["gap_before"] = \
                following["items"][0]["s"] - current["items"][-1]["e"]
    return units


def _chars(text):
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _similarity(a, b):
    import difflib
    aa, bb = _chars(a), _chars(b)
    if not aa or not bb:
        return 0.0
    return difflib.SequenceMatcher(None, aa, bb, autojunk=False).ratio()


def _unit_text(units):
    return join_words([it["w"] for unit in units for it in unit["items"]])


def _section_candidate_score(section, units):
    authored = " ".join(line["text"] for line in section["lines"])
    score = _similarity(authored, _unit_text(units))
    expected, actual = len(section["lines"]), len(units)
    score -= min(0.22, abs(expected - actual) * 0.035)
    audio_kinds = [_section_kind(tag) for unit in units for tag in unit["tags"]]
    audio_kinds = [kind for kind in audio_kinds if kind]
    if section["kind"] and audio_kinds:
        score += 0.16 if section["kind"] == audio_kinds[0] else -0.20
    # A new tagged audio section inside a candidate is strong evidence that
    # this candidate crossed a real boundary.
    internal_tags = [tag for unit in units[1:] for tag in unit["tags"]
                     if _section_kind(tag)]
    score -= min(0.36, 0.22 * len(internal_tags))
    return max(0.0, min(1.0, score))


def _match_sections(sections, units):
    """Partition audio units among authored sections with monotonic DP."""
    count_s, count_u = len(sections), len(units)
    neg = -10 ** 9
    dp = [[neg] * (count_u + 1) for _ in range(count_s + 1)]
    back = [[None] * (count_u + 1) for _ in range(count_s + 1)]
    score_cache = {}
    dp[0][0] = 0.0
    for si, section in enumerate(sections):
        expected = len(section["lines"])
        remaining_sections = count_s - si - 1
        max_take = min(count_u, max(expected * 2 + 3, expected + 5))
        weight = max(1.0, min(3.0, expected * 0.75))
        for used in range(count_u + 1):
            if dp[si][used] == neg:
                continue
            # A missing authored section is legal and does not perturb later ones.
            value = dp[si][used] - 0.55 * weight
            if value > dp[si + 1][used]:
                dp[si + 1][used] = value
                back[si + 1][used] = (used, 0, 0.0)
            # Permit small unmatched audio regions between authored sections.
            # They are emitted later as local audio fallback, not discarded.
            for skip in range(0, min(8, count_u - used - 1) + 1):
                start = used + skip
                for take in range(1, min(max_take, count_u - start) + 1):
                    candidate = units[start:start + take]
                    cache_key = (si, start, start + take)
                    if cache_key not in score_cache:
                        score_cache[cache_key] = _section_candidate_score(section, candidate)
                    confidence = score_cache[cache_key]
                    boundary_bonus = 0.0
                    if candidate[0]["section_boundary"]:
                        boundary_bonus += 0.08
                    # Reserving at least one unit per later section prevents an early
                    # weak region from greedily swallowing the rest of the song.
                    left = count_u - start - take
                    if left < remaining_sections:
                        boundary_bonus -= 0.18 * (remaining_sections - left)
                    value = (dp[si][used] + (confidence - 0.30) * weight +
                             boundary_bonus - 0.025 * abs(take - expected) -
                             0.08 * skip)
                    end = start + take
                    if value > dp[si + 1][end]:
                        dp[si + 1][end] = value
                        back[si + 1][end] = (used, start, take, confidence)

    end = max(range(count_u + 1),
              key=lambda used: dp[count_s][used] - 0.08 * (count_u - used))
    assignments = [None] * count_s
    used = end
    for si in range(count_s, 0, -1):
        record = back[si][used]
        if len(record) == 3:  # skipped authored section
            previous, take, confidence = record
            start = previous
        else:
            previous, start, take, confidence = record
        assignments[si - 1] = {"start": start, "end": start + take,
                               "confidence": confidence}
        used = previous
    return assignments


def _local_section_alignment(section, units, low_line=0.42, low_section=0.34):
    """Character-align one matched section and return groups + diagnostics."""
    import difflib
    items = [it for unit in units for it in unit["items"]]
    authored_chars, char_line, char_token = [], [], []
    lyric_tokens, parenthetical_tokens = [], []
    for li, line in enumerate(section["lines"]):
        tokens = [token for token in re.split(r"\s+", line["text"]) if _chars(token)]
        lyric_tokens.append(tokens)
        depth, paren_indexes = 0, set()
        for ti, token in enumerate(tokens):
            if depth > 0 or "(" in token:
                paren_indexes.add(ti)
            depth = max(0, depth + token.count("(") - token.count(")"))
            for ch in _chars(token):
                authored_chars.append(ch); char_line.append(li); char_token.append(ti)
        parenthetical_tokens.append(paren_indexes)
    audio_chars, char_item = [], []
    for ii, item in enumerate(items):
        for ch in _chars(item["w"]):
            audio_chars.append(ch); char_item.append(ii)

    matcher = difflib.SequenceMatcher(None, audio_chars, authored_chars, autojunk=False)
    votes = [{} for _ in items]
    token_votes = [{} for _ in items]
    matched_audio = [0] * len(items)
    matched_line = [0] * len(section["lines"])
    matched_tokens = [set() for _ in section["lines"]]
    matched = 0
    for ai, aj, size in matcher.get_matching_blocks():
        for offset in range(size):
            ii = char_item[ai + offset]
            li = char_line[aj + offset]
            votes[ii][li] = votes[ii].get(li, 0) + 1
            token_key = (li, char_token[aj + offset])
            token_votes[ii][token_key] = token_votes[ii].get(token_key, 0) + 1
            matched_audio[ii] += 1
            matched_line[li] += 1
            matched_tokens[li].add(char_token[aj + offset])
        matched += size
    section_confidence = (2.0 * matched / (len(audio_chars) + len(authored_chars))
                          if audio_chars or authored_chars else 0.0)

    if section_confidence < low_section:
        for li, line in enumerate(section["lines"]):
            if (li < len(units) and line["text"].startswith("(") and
                    line["text"].endswith(")")):
                for item in units[li]["items"]:
                    item["parenthetical"] = True
        diagnostics = []
        for li, line in enumerate(section["lines"]):
            group_items = units[li]["items"] if li < len(units) else []
            diagnostics.append(_line_diagnostic(section, li, group_items, 0.0,
                                                  "hidden-low-confidence",
                                                  [it["w"] for it in group_items],
                                                  lyric_tokens[li]))
        return [], diagnostics, section_confidence, [
            "low-confidence section hidden; full artwork retained"]

    # Unit-level votes make unmatched ad-libs stay with their local audio line.
    unit_owner, offset = [], 0
    last_owner = 0
    for unit in units:
        tally = {}
        for ii in range(offset, offset + len(unit["items"])):
            for li, count in votes[ii].items():
                tally[li] = tally.get(li, 0) + count
        owner = max(tally, key=tally.get) if tally else last_owner
        owner = max(last_owner, owner)  # monotonic inside the section
        unit_owner.extend([owner] * len(unit["items"]))
        last_owner = owner
        offset += len(unit["items"])

    owners, last_owner = [], 0
    for ii, vote in enumerate(votes):
        owner = max(vote, key=vote.get) if vote else unit_owner[ii]
        owner = max(last_owner, min(owner, len(section["lines"]) - 1))
        owners.append(owner)
        last_owner = owner

    # Do not let a long un-authored "oooooh" start the first displayed lyric.
    # It remains visible in diagnostics, and an authored vocalization still
    # matches strongly enough to be retained.
    suppressed_onset = set()
    for ii, item in enumerate(items):
        item_chars = len(_chars(item["w"]))
        reliable = item_chars and matched_audio[ii] / item_chars >= 0.60
        if reliable:
            break
        suppressed_onset.add(ii)

    groups, diagnostics = [], []
    for li, line in enumerate(section["lines"]):
        selected_indexes = [ii for ii in range(len(items))
                            if owners[ii] == li and ii not in suppressed_onset]
        selected = [items[ii] for ii in selected_indexes]
        if line["text"].startswith("(") and line["text"].endswith(")"):
            for item in selected:
                item["parenthetical"] = True
        else:
            for ii in selected_indexes:
                token_key = (max(token_votes[ii], key=token_votes[ii].get)
                             if token_votes[ii] else None)
                if (token_key and token_key[0] == li and
                        token_key[1] in parenthetical_tokens[li]):
                    items[ii]["parenthetical"] = True
        auth_len = sum(len(_chars(t)) for t in lyric_tokens[li])
        audio_len = sum(len(_chars(it["w"])) for it in selected)
        effective_matched = matched_line[li] - sum(
            votes[ii].get(li, 0) for ii in suppressed_onset)
        confidence = (2.0 * effective_matched / (auth_len + audio_len)
                      if auth_len or audio_len else 0.0)
        confidence = max(0.0, min(1.0, confidence))
        skipped = [token for ti, token in enumerate(lyric_tokens[li])
                   if ti not in matched_tokens[li]]
        unmatched = [it["w"] for ii, it in enumerate(items)
                     if owners[ii] == li and
                     _chars(it["w"]) and
                     (not matched_audio[ii] or ii in suppressed_onset)]
        method = ("local-char" if confidence >= low_line
                  else "hidden-low-confidence")
        if selected and confidence >= low_line:
            groups.append([unorphan(selected)])
        diagnostic = _line_diagnostic(section, li, selected, confidence,
                                      method, unmatched, skipped)
        if li == 0 and suppressed_onset:
            diagnostic["warnings"].append(
                "unmatched vocalization excluded from subtitle onset")
        diagnostics.append(diagnostic)
    warnings = []
    if any(line["method"] == "hidden-low-confidence" for line in diagnostics):
        warnings.append("one or more low-confidence lines hidden; full artwork retained")
    return groups, diagnostics, section_confidence, warnings


def _line_diagnostic(section, line_index, items, confidence, method,
                     unmatched, skipped):
    text = join_words([it["w"] for it in items]) if items else ""
    warnings = []
    if confidence < 0.42:
        warnings.append("low confidence")
    if items and items[-1]["e"] - items[0]["s"] < 1.0:
        warnings.append("line shorter than 1.0 seconds; renderer hides block")
    for item in items:
        warning = item.get("timing_warning")
        if warning and warning not in warnings:
            warnings.append(warning)
    return {"section_index": section["index"], "section_tag": section["tag"],
            "line_index": line_index, "authored_text": section["lines"][line_index]["text"],
            "matched_text": text, "skipped_lyric_text": skipped,
            "unmatched_audio_words": unmatched, "confidence": round(confidence, 4),
            "method": method, "warnings": warnings,
            "start": items[0]["s"] if items else None,
            "end": items[-1]["e"] if items else None}


def stable_ts_runtime_python():
    """The ML stack stays isolated from Suno Studio's stdlib-only process."""
    return CONFIG_DIR / "stable-ts-venv" / "bin" / "python"


def stable_ts_helper_path():
    return Path(__file__).resolve().with_name("stable_ts_hybrid.py")


def stable_ts_runtime_status():
    python = stable_ts_runtime_python()
    helper = stable_ts_helper_path()
    if not helper.is_file():
        return {"ready": False, "message": "stable-ts helper is missing"}
    if not python.is_file():
        return {"ready": False, "message": "local stable-ts runtime is not installed"}
    try:
        probe = subprocess.run(
            [str(python), "-c", "import stable_whisper; print('ready')"],
            capture_output=True, text=True, timeout=20)
        if probe.returncode == 0:
            return {"ready": True, "message": "local stable-ts runtime is ready"}
        detail = (probe.stderr or probe.stdout or "import failed").strip().splitlines()[-1]
        return {"ready": False, "message": f"stable-ts runtime error: {detail[:160]}"}
    except Exception as error:
        return {"ready": False, "message": f"stable-ts runtime error: {error}"}


def build_stable_ts_hybrid(mp3, lyrics_text, aligned, ffmpeg, scratch_dir=None,
                           log=None):
    """Run/cached local forced alignment and return renderer-compatible words.

    Weak stable-ts lines are repaired only inside trusted neighboring anchors.
    The helper compares local Whisper with Suno's original words and may hide a
    line, but it cannot move any later confirmed line.
    """
    mp3 = Path(mp3)
    helper = stable_ts_helper_path()
    python = stable_ts_runtime_python()
    if not python.is_file() or not helper.is_file():
        raise RuntimeError(stable_ts_runtime_status()["message"])
    stat = mp3.stat()
    source_digest = hashlib.sha256(json.dumps(aligned or [], sort_keys=True).encode()).hexdigest()
    signature = {"audio_size": stat.st_size, "audio_mtime_ns": stat.st_mtime_ns,
                 "lyrics_sha256": hashlib.sha256(lyrics_text.encode()).hexdigest(),
                 "source_sha256": source_digest,
                 "helper_mtime_ns": helper.stat().st_mtime_ns,
                 "model": "base.en",
                 "repair_mode": CONFIG.get("hybrid_repair", "local")}
    cache = mp3.with_suffix(".stable-ts-hybrid.json")
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if (cached.get("signature") == signature and cached.get("alignedWords") and
                    not cached.get("retry_cloud")):
                if log:
                    log(f"using cached stable-ts hybrid timing ({len(cached['alignedWords'])} words)")
                return cached
        except Exception:
            pass
    work = Path(scratch_dir) if scratch_dir else Path(tempfile.mkdtemp(prefix="suno-stable-ts-request-"))
    work.mkdir(parents=True, exist_ok=True)
    request_path = work / "stable-ts-request.json"
    fallback_lines = []
    if aligned and lyrics_text:
        baseline = align_lyrics(aligned, lyrics_text, method="section")
        baseline_groups = baseline.get("groups") or []
        used_groups = set()
        for line_index, diagnostic in enumerate(baseline.get("lines") or []):
            if diagnostic.get("start") is None or diagnostic.get("end") is None:
                continue
            match_index = next((index for index, rows in enumerate(baseline_groups)
                                if index not in used_groups and rows and rows[0] and
                                abs(rows[0][0]["s"] - diagnostic["start"]) < 0.03 and
                                abs(rows[-1][-1]["e"] - diagnostic["end"]) < 0.03), None)
            if match_index is None:
                continue
            used_groups.add(match_index)
            items = [item for row in baseline_groups[match_index] for item in row]
            fallback_lines.append({"line_index": line_index,
                                   "section_index": diagnostic.get("section_index"),
                                   "authored_text": diagnostic.get("authored_text"),
                                   "words": [{"word": item["w"], "start": item["s"],
                                              "end": item["e"],
                                              "parenthetical": bool(item.get("parenthetical"))}
                                             for item in items]})
    request = {"audio": str(mp3), "lyrics": lyrics_text,
               "alignedWords": aligned or [], "ffmpeg": str(ffmpeg),
               "sectionFallbackLines": fallback_lines,
               "cloudRepair": CONFIG.get("hybrid_repair") == "cloud",
               "model": "base.en", "model_dir": str(CONFIG_DIR / "stable-ts-models"),
               "signature": signature}
    request_path.write_text(json.dumps(request), encoding="utf-8")
    (CONFIG_DIR / "stable-ts-models").mkdir(parents=True, exist_ok=True)
    if log:
        log("running local stable-ts alignment (first use downloads the model)")
    helper_env = os.environ.copy()
    # Finder-launched macOS apps receive a minimal PATH. stable-ts invokes the
    # bare commands `ffmpeg` and `ffprobe` internally, so expose the directory
    # of the absolute ffmpeg executable Suno Studio already discovered.
    ffmpeg_path = Path(ffmpeg)
    if ffmpeg_path.is_absolute():
        inherited_path = helper_env.get("PATH", "")
        helper_env["PATH"] = str(ffmpeg_path.parent) + (
            os.pathsep + inherited_path if inherited_path else "")
    helper_env.pop("SUNO_STUDIO_OPENAI_KEY", None)
    if request["cloudRepair"]:
        key = (CONFIG.get("openai_key") or "").strip()
        if key:
            helper_env["SUNO_STUDIO_OPENAI_KEY"] = key
        if log:
            log("weak lines will use bounded hosted whisper-1 repair")
    process = subprocess.run([str(python), str(helper), str(request_path), str(cache)],
                             capture_output=True, text=True, timeout=20 * 60,
                             env=helper_env)
    if process.returncode != 0 or not cache.is_file():
        detail = (process.stderr or process.stdout or "stable-ts helper failed").strip()
        raise RuntimeError(detail[-1200:])
    result = json.loads(cache.read_text(encoding="utf-8"))
    if not result.get("alignedWords"):
        raise RuntimeError("stable-ts hybrid produced no safe timed words")
    if log:
        log(f"stable-ts aligned {result.get('rendered_source_lines', 0)}/"
            f"{result.get('authored_lines', 0)} lines; repaired {len(result.get('repairs', []))}")
    return result


def align_lyrics(aligned, lyrics_text, method="section", max_chars=46):
    """Return renderer groups plus section/line diagnostics.

    ``method='legacy'`` is the selectable whole-song baseline. The section
    method never falls back for the whole song merely because one region is bad.
    """
    if method == "legacy":
        groups = legacy_lines_from_lyrics(aligned, lyrics_text, max_chars=max_chars)
        return {"groups": groups, "method": "legacy-global-char",
                "overall_confidence": None, "sections": [], "lines": [],
                "unmatched_audio_words": [], "skipped_lyric_text": [],
                "warnings": []}
    # Hybrid timing is produced before this text-only matcher is entered. If
    # called directly, retain the safe section matcher rather than treating an
    # unknown method as a new global algorithm.
    sections = parse_authored_lyrics(lyrics_text)
    units = segment_audio_chunks(aligned)
    if not sections or not units:
        return {"groups": None, "method": "section-dp", "overall_confidence": 0.0,
                "sections": [], "lines": [], "unmatched_audio_words": [],
                "skipped_lyric_text": [], "warnings": ["missing lyrics or timed words"]}
    assignments = _match_sections(sections, units)
    groups, section_diags, line_diags, warnings = [], [], [], []
    unmatched_regions = []
    weighted, weight_total = 0.0, 0
    cursor = 0
    for section, assignment in zip(sections, assignments):
        if assignment["start"] > cursor:
            region = units[cursor:assignment["start"]]
            unmatched_regions.extend(region)
            groups.extend([[unorphan(unit["items"])] for unit in region if unit["items"]])
            warnings.append("unmatched audio region retained with audio line breaks")
        matched_units = units[assignment["start"]:assignment["end"]]
        local_groups, local_lines, confidence, local_warnings = \
            _local_section_alignment(section, matched_units) if matched_units else \
            ([], [_line_diagnostic(section, li, [], 0.0, "skipped-section", [],
                                   re.split(r"\s+", line["text"]))
                  for li, line in enumerate(section["lines"])], 0.0,
             ["authored section was not matched"])
        groups.extend(local_groups)
        line_diags.extend(local_lines)
        chars = sum(len(_chars(line["text"])) for line in section["lines"])
        weighted += confidence * chars; weight_total += chars
        section_diag = {"index": section["index"], "tag": section["tag"],
                        "kind": section["kind"], "confidence": round(confidence, 4),
                        "method": ("local-char" if confidence >= 0.34
                                   else "hidden-low-confidence"),
                        "audio_unit_range": [assignment["start"], assignment["end"]],
                        "warnings": local_warnings,
                        "lines": local_lines}
        section_diags.append(section_diag)
        warnings.extend(f"{section['tag'] or 'section ' + str(section['index'])}: {w}"
                        for w in local_warnings)
        cursor = max(cursor, assignment["end"])
    if cursor < len(units):
        suffix = units[cursor:]
        unmatched_regions.extend(suffix)
        groups.extend([[unorphan(unit["items"])] for unit in suffix if unit["items"]])
        warnings.append("unmatched trailing audio retained with audio line breaks")
    skipped = [token for line in line_diags for token in line["skipped_lyric_text"]]
    unmatched = [word for line in line_diags for word in line["unmatched_audio_words"]]
    unmatched.extend(it["w"] for unit in unmatched_regions for it in unit["items"]
                     if _chars(it["w"]))
    base_confidence = weighted / weight_total if weight_total else 0.0
    unmatched_chars = sum(len(_chars(it["w"])) for unit in unmatched_regions
                          for it in unit["items"])
    audio_precision = (2.0 * weight_total / (2.0 * weight_total + unmatched_chars)
                       if weight_total else 0.0)
    return {"groups": groups, "method": "section-dp-local-char",
            "overall_confidence": round(base_confidence * audio_precision, 4),
            "sections": section_diags, "lines": line_diags,
            "unmatched_audio_words": unmatched, "skipped_lyric_text": skipped,
            "warnings": warnings}


def lines_from_lyrics(aligned, lyrics_text, max_chars=46, method=None,
                      return_result=False):
    """Compatibility wrapper returning the historical renderer group shape."""
    selected = method or CONFIG.get("lyric_aligner", "section")
    result = align_lyrics(aligned, lyrics_text, method=selected, max_chars=max_chars)
    return result if return_result else result["groups"]


def group_lyric_lines(aligned, max_chars=46, gap=1.1):
    """alignedWords -> list of lines.

    Suno marks its own line breaks with a newline *before* the word. The
    break must be detected on the raw string: .strip() eats a leading \\n,
    which silently merged every line into a rolling window."""
    lines, cur = [], []
    for w in aligned:
        raw = w.get("word") or ""
        # NB: check raw, not raw.strip()
        forced = ("\n" in raw) or bool(re.search(r"\[[^\]]+\]", raw))
        clean = re.sub(r"\[[^\]]+\]", " ", raw)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            continue
        try:
            item = {"w": clean, "s": float(w["startS"]), "e": float(w["endS"])}
            if w.get("parenthetical"):
                item["parenthetical"] = True
        except (KeyError, TypeError, ValueError):
            continue
        if item["e"] < item["s"]:
            item["e"] = item["s"] + 0.2
        if cur:
            # Suno's own breaks lead; length is only a safety net for
            # unusually long lines, and silence catches missing markers.
            too_long = len(join_words([x["w"] for x in cur] + [clean])) > max_chars
            silence = item["s"] - cur[-1]["e"] > gap
            if forced or too_long or silence:
                lines.append(cur)
                cur = []
        cur.append(item)
    if cur:
        lines.append(cur)
    return [[unorphan(ln)] for ln in lines if ln]


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Now,{font},{size},{hi},{lo},&H00000000,&HC8000000,-1,0,0,0,100,100,0.4,0,1,4,2.5,8,{margin},{margin},{vmargin},1
Style: Banner,{font},72,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,-1,0,0,0,100,100,0,0,1,4,3,8,80,80,70,1
Style: BannerSub,{font},36,&H99FFFFFF,&H99FFFFFF,&H00000000,&HB4000000,0,0,0,0,100,100,0,0,1,3,2,8,80,80,165,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def fit_fontsize(groups, width=1920, margin=56, lo=28, hi=64):
    """
    Pick the largest size at which the LONGEST lyric line still fits on one
    row. Wrapping is gone, so the type adapts to the song instead.
    0.52em is a decent average advance for a bold sans.
    """
    longest = 0
    for g in groups:
        for row in g:
            longest = max(longest, len(join_words([x["w"] for x in row])))
    if not longest:
        return hi
    return max(lo, min(hi, int((width - 2 * margin) / longest / 0.52)))


def build_karaoke_ass(aligned, font="Helvetica", hi="&H00A6D322", lo="&H00C8C8C8",
                      lead=0.18, tail=0.45, banner=None, lyrics_text="",
                      aligner_method=None, paren_hi="&H0042B9F5",
                      paren_lo="&H0080BFE0"):
    """ASS with \\kf karaoke fills. Colours are &HAABBGGRR - BGR, not RGB.

    banner=(title, subtitle) draws the title through libass instead of
    drawtext, for ffmpeg builds without freetype."""
    groups = lines_from_lyrics(aligned, lyrics_text, method=aligner_method)
    # None means authored alignment was unavailable. An empty list is
    # intentional: every candidate region was too unreliable to display.
    if groups is None:
        groups = group_lyric_lines(aligned)
    margin = 56
    size = fit_fontsize(groups, margin=margin)
    # Alignment 8 (top-centre) + a margin puts the baseline at a known
    # height. Dead centre sat above the calm band the artwork leaves.
    vmargin = int(1080 * float(CONFIG.get("lyric_y", 0.680)))
    out = [ASS_HEAD.format(font=font, hi=hi, lo=lo, size=size,
                           margin=margin, vmargin=vmargin)]
    if banner:
        span = (groups[-1][-1][-1]["e"] + 10) if groups else 600
        btitle, bsub = banner
        out.append(f"Dialogue: 0,{ass_time(0)},{ass_time(span)},Banner,,0,0,0,,"
                   f"{ass_escape(btitle)}")
        if (bsub or "").strip():
            out.append(f"Dialogue: 0,{ass_time(0)},{ass_time(span)},BannerSub,,0,0,0,,"
                       f"{ass_escape(bsub)}")

    MIN_TIMED_BLOCK = 1.0
    MIN_ON_SCREEN = 1.0
    last_event_end = 0.0
    rendered = 0
    for i, rows in enumerate(groups):
        flat = [it for r in rows for it in r]
        # Extremely short blocks are usually compressed or misplaced Suno
        # responses. Retain them in alignment diagnostics, but do not flash
        # questionable lyrics in the finished video.
        if flat[-1]["e"] - flat[0]["s"] < MIN_TIMED_BLOCK:
            continue
        previous_word_end = (groups[i - 1][-1][-1]["e"] if i else 0.0)
        start = max(0.0, flat[0]["s"] - lead, last_event_end + 0.01,
                    previous_word_end + (0.01 if i else 0.0))
        end = flat[-1]["e"] + tail
        nxt = groups[i + 1][0][0]["s"] if i + 1 < len(groups) else None
        if nxt is not None:
            # must be gone before the NEXT event fades in, or libass stacks them
            # Never end the current event before its own final timed word.
            end = min(end, max(flat[-1]["e"], nxt - lead - 0.06))
        if end - start < MIN_ON_SCREEN:
            # Buy only genuinely available time before the event. Never push a
            # short response forward into the following lyric merely to reach
            # the preferred duration.
            start = max(last_event_end + 0.01, 0.0,
                        min(start, end - MIN_ON_SCREEN))
        if end <= start:
            end = start + 0.05
        last_event_end = end

        parts, prev, plain = [], start, ""
        paren_active = False
        for ri, row in enumerate(rows):
            if ri:
                parts.append("\\N")
            first_in_row = True
            for it in row:
                hold = max(0, int(round((it["s"] - prev) * 100)))
                add_space = not first_in_row and needs_space(plain, it["w"])
                if hold and add_space:
                    # A karaoke tag with no following character is overwritten
                    # by the next tag. Attach the pause to the visible space so
                    # the following word still begins at its absolute timestamp.
                    parts.append("{\\kf%d} " % hold)
                    plain += " "
                elif hold:
                    # Leading delays and contraction gaps have no ordinary
                    # space to carry timing; a zero-width character does.
                    parts.append("{\\kf%d}\u200b" % hold)
                elif add_space:
                    parts.append(" ")
                    plain += " "
                first_in_row = False
                wants_paren = bool(it.get("parenthetical"))
                if wants_paren != paren_active:
                    if wants_paren:
                        parts.append("{\\1c%s&\\2c%s&}" % (paren_hi, paren_lo))
                    else:
                        parts.append("{\\1c%s&\\2c%s&}" % (hi, lo))
                    paren_active = wants_paren
                parts.append("{\\kf%d}%s" % (max(1, int(round((it["e"] - it["s"]) * 100))),
                                             ass_escape(it["w"])))
                plain += it["w"]
                prev = it["e"]
        out.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Now,,0,0,0,,"
                   f"{{\\fad(140,140)}}{''.join(parts)}")
        rendered += 1
    return "\n".join(out) + "\n", rendered


def ass_escape(s):
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


# Each kie.ai image model takes a DIFFERENT input schema. Sending one shape to
# all of them is why this failed: imagen4 wants aspect_ratio, the others want
# image_size, and a mismatch can surface as a bare "internal error".
# If artwork fails repeatedly, stop trying for the session and fall back to
# the generated gradient rather than adding latency to every render.
ART_HEALTH = {"fails": 0, "off": False}
ART_FAIL_LIMIT = 2


def art_available():
    return not ART_HEALTH["off"]


def note_art_failure(err):
    ART_HEALTH["fails"] += 1
    if ART_HEALTH["fails"] >= ART_FAIL_LIMIT and not ART_HEALTH["off"]:
        ART_HEALTH["off"] = True
        print(f"[art] disabling AI artwork for this session after "
              f"{ART_HEALTH['fails']} failures ({err}). Restart to re-enable.")


def note_art_success():
    ART_HEALTH["fails"] = 0
    ART_HEALTH["off"] = False


def openai_image(key, prompt, dest, model="gpt-image-2", timeout=240, status=None):
    """
    OpenAI Images API. Synchronous - the image comes back in the response, so
    there is no task to poll and no async failure mode (which is exactly what
    broke on the other provider).

    Sizes are tried widest-first and the request self-corrects: if the model
    rejects a parameter we drop it and retry rather than failing the render.
    """
    url = "https://api.openai.com/v1/images/generations"
    attempts = [
        # 1792x1024 is 1.75:1 - almost 16:9, so the crop to 1920x1080 shaves
        # ~13px instead of ~100px off the top, which would clip the title.
        {"model": model, "prompt": prompt, "size": "1792x1024"},
        {"model": model, "prompt": prompt, "size": "1536x1024"},
        {"model": model, "prompt": prompt, "size": "1024x1024"},
        {"model": model, "prompt": prompt},
    ]
    last = ""
    for attempt, body in enumerate(attempts, 1):
        if status:
            status(f"requesting image ({attempt}/{len(attempts)})")
        try:
            res = api_json("POST", url, key, body, timeout=timeout)
        except Exception as e:
            last = str(e)
            lower = last.lower()
            # Only an unsupported image parameter merits another request. A
            # timeout, service outage, auth failure, or billing failure used
            # to trigger all four variants (up to 16 minutes of apparent UI
            # hanging) without improving the result.
            if not any(w in lower for w in ("invalid size", "unsupported size",
                                            "invalid parameter", "unsupported parameter",
                                            "must be one of", "size is not supported")):
                raise RuntimeError(f"OpenAI image request failed: {last[:300]}")
            print(f"[art] openai {sorted(body)} -> {last[:160]}")
            continue
        if res.get("error"):
            last = str(res["error"].get("message") or res["error"])
            print(f"[art] openai {sorted(body)} -> {last[:160]}")
            if "size" not in last.lower() and "parameter" not in last.lower():
                raise RuntimeError(f"OpenAI image request failed: {last[:300]}")
            continue
        items = res.get("data") or []
        if not items:
            last = f"no data in reply: {json.dumps(res)[:200]}"
            continue
        first = items[0]
        if first.get("b64_json"):
            import base64
            Path(dest).write_bytes(base64.b64decode(first["b64_json"]))
            if status:
                status("image received; saving")
            return dest
        if first.get("url"):
            if status:
                status("image received; downloading")
            download(first["url"], dest)
            return dest
        last = f"no image in reply: {json.dumps(first)[:200]}"
    raise RuntimeError(last or "OpenAI returned no image")


def _openai_multipart(fields, files):
    """Build a small multipart body without adding a third-party dependency."""
    boundary = "----suno-studio-" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode(), b"\r\n",
        ])
    for name, path in files.items():
        path = Path(path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            (f'Content-Disposition: form-data; name="{name}"; '
             f'filename="{path.name}"\r\n').encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(), b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


def image_dimensions(ff, image):
    """Return still-image dimensions through the ffprobe beside FFmpeg."""
    probe = str(Path(ff).with_name("ffprobe"))
    if not Path(probe).exists():
        from shutil import which
        probe = which("ffprobe") or ""
    if not probe:
        raise RuntimeError("ffprobe is required for the lyric focus-band edit")
    p = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(image)],
        capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise RuntimeError((p.stderr or "could not inspect artwork")[:200])
    stream = (json.loads(p.stdout).get("streams") or [{}])[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width < 64 or height < 64:
        raise RuntimeError("artwork dimensions are invalid")
    return width, height


def make_lyric_band_mask(ff, source, dest, top=0.64, bottom=0.79):
    """Make an OpenAI edit mask: opaque except for the exact lyric strip."""
    width, height = image_dimensions(ff, source)
    y = max(0, min(height - 1, round(height * float(top))))
    band_h = max(1, min(height - y, round(height * (float(bottom) - float(top)))))
    fc = (f"color=white:s={width}x{height}:d=1,format=rgb24[rgb];"
          f"color=white:s={width}x{height}:d=1,format=gray,"
          f"drawbox=x=0:y={y}:w=iw:h={band_h}:color=black:t=fill[alpha];"
          "[rgb][alpha]alphamerge[out]")
    run_ffmpeg(ff, ["-f", "lavfi", "-i", f"color=white:s={width}x{height}:d=1",
                    "-filter_complex", fc, "-map", "[out]", "-frames:v", "1",
                    str(dest)], "lyric focus mask")
    return dest


def openai_lyric_band_edit(key, source, dest, ff, model="gpt-image-2", timeout=300):
    """Ask GPT Image to simplify only the band later composited over lyrics."""
    source, dest = Path(source), Path(dest)
    mask = dest.with_name(dest.stem + " - mask.png")
    make_lyric_band_mask(ff, source, mask)
    prompt = (
        "Edit only the transparent masked horizontal region. Replace it with "
        "calm, low-detail negative space visually continuous with this exact "
        "album artwork: a subtle dark field of its existing colours and texture, "
        "suitable behind highly legible karaoke lyrics. No text, letters, people, "
        "faces, instruments, recognizable objects, bright lights, flares, or "
        "strong patterns inside the masked region. Preserve all unmasked artwork "
        "and typography."
    )
    width, height = image_dimensions(ff, source)
    size = f"{width}x{height}"
    boundary, data = _openai_multipart(
        {"model": model, "prompt": prompt, "size": size, "quality": "medium"},
        {"image[]": source, "mask": mask})
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/edits", data=data, method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"OpenAI focus-band edit HTTP {e.code}: {detail}")
    finally:
        try:
            mask.unlink()
        except OSError:
            pass
    item = (payload.get("data") or [{}])[0]
    if item.get("b64_json"):
        import base64
        dest.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        download(item["url"], dest)
    else:
        error = payload.get("error") or "OpenAI returned no edited image"
        raise RuntimeError(str(error)[:500])
    return dest


def image_prompt_fragments():
    """Return defaults overlaid by valid per-user English customisations."""
    custom = CONFIG.get("image_prompt_fragments") or {}
    return {key: str(custom.get(key) or default)
            for key, default in IMAGE_PROMPT_DEFAULTS.items()}


def validate_image_prompt_fragments(fragments):
    """Warnings only: custom copy must never make Settings impossible to save."""
    warnings = []
    for key, value in (fragments or {}).items():
        if key not in IMAGE_PROMPT_DEFAULTS:
            warnings.append(f"unknown fragment: {key}")
            continue
        found = set(re.findall(r"{([^{}]+)}", str(value)))
        unknown = found - IMAGE_FRAGMENT_PLACEHOLDERS.get(key, set())
        missing = IMAGE_FRAGMENT_PLACEHOLDERS.get(key, set()) - found
        if unknown:
            warnings.append(f"{key}: unrecognised placeholder(s): " + ", ".join(sorted(unknown)))
        if missing:
            warnings.append(f"{key}: missing required placeholder(s): " + ", ".join(sorted(missing)))
    return warnings


def assemble_image_prompt(title, style, infographic="", draw_title=True, tagline="", fragments=None):
    """Assemble one job's prompt.  `negatives` is returned for provenance only.

    GPT Image has no negative-prompt field; callers must fold any desired
    negative guidance into prompt text explicitly rather than pretending it is
    an API argument.
    """
    def fill(value, **values):
        # Settings validation is advisory. A typo must not crash a queued job.
        return re.sub(r"{([^{}]+)}", lambda m: str(values.get(m.group(1), m.group(0))), value)

    f = dict(IMAGE_PROMPT_DEFAULTS)
    f.update(fragments or image_prompt_fragments())
    look, spec = (style or "").strip(), (infographic or "").strip()
    scene = fill(f["scene_base"], style=(f"The music is {look}. " if look else ""))
    if spec:
        scene += fill(f["screen_block"], infographic=spec)
    if draw_title:
        text_part = fill(f["title_block"], title=title or "Untitled")
        if (tagline or "").strip():
            text_part += fill(f["tagline_block"], tagline=tagline.strip())
        neg = f["negatives_with_title"]
    elif spec:
        text_part, neg = f["no_title_block"], f["negatives_no_title"]
    else:
        text_part, neg = f["no_title_no_spec_block"], f["negatives_no_title_no_spec"]
    # Negative prompts are unsupported by this endpoint. Include the list in
    # the sole `prompt` field so it actually reaches the model.
    return (scene + text_part + " Avoid: " + neg)[:4500], neg


def generate_background_image(key, title, style, dest, model=None, timeout=240,
                              draw_title=True, tagline="", infographic="", prompt=None,
                              fragments=None, probe=False, status=None):
    """
    Cover art via kie.ai's market API - same key as the music.

        POST /api/v1/jobs/createTask   ->  taskId
        GET  /api/v1/jobs/recordInfo   ->  image url

    With draw_title the model is asked to letter the title INTO the artwork.
    Ideogram v3 is the default because it renders type legibly; most models
    turn text into mush.
    """
    model = model or (CONFIG.get("openai_image_model") or "gpt-image-2")
    if prompt is None:
        prompt, _neg = assemble_image_prompt(title, style, infographic,
                                              draw_title, tagline, fragments)

    okey = (CONFIG.get("openai_key") or "").strip()
    if not okey:
        raise RuntimeError("No OpenAI key saved. Settings > OpenAI API key.")
    print(f"[art] openai {model} prompt={prompt[:110]!r}")
    out = openai_image(okey, prompt, dest, model=model, timeout=timeout, status=status)
    note_art_success()
    return out


def ff_path(p):
    """Escape a path for use inside a filtergraph option value."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_drawtext_lyrics(aligned, w, h, font, tmpdir, accent="0x22d3a6",
                          lyrics_text=""):
    """
    Lyrics without libass: one drawtext per line, gated by `enable=between()`.

    No per-word karaoke sweep - drawtext can't fill mid-word - but the current
    line appears and leaves on the beat, and the next line previews faintly.
    Used when ffmpeg has freetype but no libass.
    """
    groups = lines_from_lyrics(aligned, lyrics_text)
    if groups is None:
        groups = group_lyric_lines(aligned)
    lines = [[it for r in g for it in r] for g in groups]
    parts, files = [], []
    fs = max(20, int(h * 0.072))
    fs2 = max(14, int(h * 0.040))
    fp = ff_path(font)
    for i, ln in enumerate(lines):
        text = " ".join(x["w"] for x in ln)
        s = max(0.0, ln[0]["s"] - 0.15)
        e = ln[-1]["e"] + 0.40
        if i + 1 < len(lines):
            e = min(e, lines[i + 1][0]["s"] - 0.05)
        if e <= s:
            e = s + 0.40
        f = tmpdir / f"_lyr_{i:04d}.txt"
        f.write_text(text, encoding="utf-8")
        files.append(f)
        parts.append(
            f"drawtext=fontfile='{fp}':textfile='{ff_path(f)}':expansion=none"
            f":fontcolor=white:fontsize={fs}:x=(w-text_w)/2:y=(h-text_h)/2"
            f":borderw={max(2, fs // 20)}:bordercolor=black@0.8"
            f":shadowcolor=black@0.5:shadowx=0:shadowy=3"
            f":enable='between(t,{s:.2f},{e:.2f})'")
        if i + 1 < len(lines):
            nxt = " ".join(x["w"] for x in lines[i + 1])
            nf = tmpdir / f"_nxt_{i:04d}.txt"
            nf.write_text(nxt, encoding="utf-8")
            files.append(nf)
            parts.append(
                f"drawtext=fontfile='{fp}':textfile='{ff_path(nf)}':expansion=none"
                f":fontcolor=white@0.45:fontsize={fs2}:x=(w-text_w)/2:y=h*0.70"
                f":borderw=2:bordercolor=black@0.6"
                f":enable='between(t,{s:.2f},{e:.2f})'")
    return ",".join(parts), files, len(lines)


def palette_for(title):
    h = 0
    for ch in (title or "x"):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return PALETTES[h % len(PALETTES)]


def make_background(ff, out_png, title, subtitle, cover=None, height=1080, art=False,
                    scratch_dir=None):
    """Colourful still, degrading gracefully on ffmpeg builds that lack the
    fancier filters. Returns (path, drew_title)."""
    w, h = int(height * 16 / 9), int(height)
    c0, c1, c2 = palette_for(title)
    have = ffmpeg_filters(ff)

    def ok(name):
        return (not have) or (name in have)

    if ok("gradients"):
        grad = (f"gradients=s={w}x{h}:c0={c0}:c1={c1}:c2={c2}:nb_colors=3"
                f":x0=0:y0={h}:x1={w}:y1=0:duration=1")
    else:
        grad = f"color=c={c1}:s={w}x{h}:d=1"          # flat but never black

    blur = "gblur=sigma={s}" if ok("gblur") else ("boxblur={s}:1" if ok("boxblur") else "")
    post = ",vignette=angle=PI/4.2" if ok("vignette") else ""

    # "Untitled" is a placeholder, not a title - drawing it looks like a bug,
    # which is exactly how it looked stamped across the album art.
    if (title or "").strip().lower() in ("", "untitled"):
        title = ""
    font = find_font() if ok("drawtext") and title.strip() else None
    txt = ""
    if font:
        scratch_dir = Path(scratch_dir or out_png.parent)
        scratch_dir.mkdir(parents=True, exist_ok=True)
        # textfile= avoids all the ':' and quote escaping traps in text=
        tf = scratch_dir / "_title.txt"
        tf.write_text((title or "").strip() or "Untitled", encoding="utf-8")
        sf = scratch_dir / "_sub.txt"
        sf.write_text((subtitle or "").strip(), encoding="utf-8")
        fsz, ssz = int(h * 0.105), int(h * 0.035)
        # drawn twice: a soft dark bloom underneath, then the crisp face
        txt = (f",drawtext=fontfile='{ff_path(font)}':textfile='{ff_path(tf)}':expansion=none"
               f":fontcolor=black@0.33:fontsize={fsz}:x=(w-text_w)/2:y=h*0.085+{max(3, int(h*0.006))}"
               f":borderw={max(4, int(h * 0.010))}:bordercolor=black@0.33"
               f",drawtext=fontfile='{ff_path(font)}':textfile='{ff_path(tf)}':expansion=none"
               f":fontcolor=white:fontsize={fsz}:x=(w-text_w)/2:y=h*0.085"
               f":borderw={max(2, int(h * 0.0028))}:bordercolor=black@0.55"
               f":shadowcolor=black@0.45:shadowx=0:shadowy={max(2, int(h*0.004))}")
        if (subtitle or "").strip():
            txt += (f",drawtext=fontfile='{ff_path(font)}':textfile='{ff_path(sf)}':expansion=none"
                    f":fontcolor=white@0.6:fontsize={ssz}:x=(w-text_w)/2"
                    f":y=h*0.11+{int(fsz*1.26)}:shadowcolor=black@0.55:shadowx=0:shadowy=3")

    eq_c = ((",eq=saturation=1.12:contrast=1.02:brightness=-0.10" if art else
             ",eq=saturation=1.35:contrast=1.05:brightness=-0.05") if ok("eq") else "")
    eq_g = ",eq=saturation=1.2:contrast=1.04" if ok("eq") else ""

    if cover and Path(cover).exists() and art:
        # The generated image is the background—not a texture for a gradient.
        # Preserve its detail and colour, applying only restrained sharpening
        # after the small aspect-fill upscale/crop.
        sharpen = ",unsharp=5:5:0.45:3:3:0.15" if ok("unsharp") else ""
        fc = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
              f"{sharpen}{txt}[out]")
        args = ["-i", str(cover), "-filter_complex", fc, "-map", "[out]",
                "-frames:v", "1", str(out_png)]
    elif cover and Path(cover).exists() and ok("blend"):
        # Suno cover images are generally small and soft; blur them heavily so
        # the crop/upscale reads as an intentional atmospheric background.
        b = ("," + blur.format(s=max(18, int(h / 30)))) if blur else ""
        fc = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
              f"{b}{eq_c}[bg];"
              f"[1:v]trim=end_frame=1,setpts=PTS-STARTPTS[gr];"
              f"[bg][gr]blend=all_mode=softlight:all_opacity=0.45{post}{txt}[out]")
        args = ["-i", str(cover), "-f", "lavfi", "-i", grad,
                "-filter_complex", fc, "-map", "[out]", "-frames:v", "1", str(out_png)]
    else:
        b = ("," + blur.format(s=max(10, int(h / 60)))) if blur else ""
        fc = (f"[0:v]trim=end_frame=1,setpts=PTS-STARTPTS{b}{eq_g}{post}{txt}[out]")
        args = ["-f", "lavfi", "-i", grad, "-filter_complex", fc,
                "-map", "[out]", "-frames:v", "1", str(out_png)]
    run_ffmpeg(ff, args, "background render")
    return out_png, bool(font)


def visualizer_chain(mode, w, vh, accent="0x22d3a6"):
    """Boost only the copy feeding the visualiser - output audio is untouched.

    The mono downmix matters: on a stereo track showfreqs/showwaves colour each
    channel separately, and the un-named second channel defaults to white -
    which is what made the bars look grey."""
    mono = "aformat=channel_layouts=mono"
    if mode == "off":
        return None
    if mode == "wave":
        return (f"{mono},volume=5,showwaves=s={w}x{vh}:mode=cline:scale=sqrt:"
                f"colors={accent},format=yuva420p,colorchannelmixer=aa=0.75")
    return (f"{mono},volume=8,showfreqs=s={w}x{vh}:mode=bar:ascale=log:fscale=log:"
            f"win_size=1024:colors={accent},format=yuva420p,colorchannelmixer=aa=0.7")


def dominant_art_accent(rgb_bytes, fallback="0x22D3A6"):
    """Choose a bright, saturated accent hue from sampled RGB pixels."""
    if not rgb_bytes or len(rgb_bytes) < 3:
        return fallback
    buckets = {}
    usable = len(rgb_bytes) - (len(rgb_bytes) % 3)
    for i in range(0, usable, 3):
        r, g, b = (rgb_bytes[i] / 255.0, rgb_bytes[i + 1] / 255.0,
                   rgb_bytes[i + 2] / 255.0)
        hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
        if saturation < 0.25 or value < 0.18 or value > 0.99:
            continue
        bucket = int(hue * 24) % 24
        weight = (saturation ** 1.5) * (0.4 + value)
        total, hs, ss, vs = buckets.get(bucket, (0.0, 0.0, 0.0, 0.0))
        buckets[bucket] = (total + weight, hs + hue * weight,
                           ss + saturation * weight, vs + value * weight)
    if not buckets:
        return fallback
    total, hs, ss, vs = max(buckets.values(), key=lambda row: row[0])
    hue = hs / total
    saturation = max(0.72, min(0.95, ss / total))
    # Keep every chosen hue bright enough to read over the artwork.
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, 0.95)
    return f"0x{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"


def art_accent_color(ff, artwork, fallback="0x22D3A6"):
    """Sample a tiny RGB thumbnail through FFmpeg; fail safely to fallback."""
    if not ff or not artwork or not Path(artwork).exists():
        return fallback
    try:
        result = subprocess.run(
            [str(ff), "-hide_banner", "-loglevel", "error", "-i", str(artwork),
             "-vf", "scale=64:64:force_original_aspect_ratio=decrease",
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            capture_output=True, timeout=15)
        if result.returncode == 0:
            return dominant_art_accent(result.stdout, fallback)
    except (OSError, subprocess.SubprocessError):
        pass
    return fallback


def ass_interlude_windows(ass_path, min_gap=4.0):
    """Return internal lyric-free windows from ASS `Now` events.

    Intro and outro silence are intentionally excluded: an interlude must sit
    between two rendered lyric events. Overlapping events are coalesced before
    gaps are measured so a short parenthetical cannot create a false window.
    """
    if not ass_path:
        return []
    try:
        text = Path(ass_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError, ValueError):
        return []

    def seconds(value):
        try:
            hour, minute, second = value.strip().split(":", 2)
            return int(hour) * 3600 + int(minute) * 60 + float(second)
        except (TypeError, ValueError):
            return None

    spans = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10 or fields[3].strip() != "Now":
            continue
        start, end = seconds(fields[1]), seconds(fields[2])
        if start is not None and end is not None and end > start:
            spans.append((start, end))
    if len(spans) < 2:
        return []
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [(round(end, 3), round(next_start, 3))
            for (_, end), (next_start, _) in zip(merged, merged[1:])
            if next_start - end >= float(min_gap)]


def ass_lyric_windows(ass_path, merge_gap=0.9):
    """Return chronological `Now` spans, joining nearly continuous lines."""
    if not ass_path:
        return []
    try:
        text = Path(ass_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError, ValueError):
        return []

    def seconds(value):
        try:
            hour, minute, second = value.strip().split(":", 2)
            return int(hour) * 3600 + int(minute) * 60 + float(second)
        except (TypeError, ValueError):
            return None

    spans = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10 or fields[3].strip() != "Now":
            continue
        start, end = seconds(fields[1]), seconds(fields[2])
        if start is not None and end is not None and end > start:
            spans.append((start, end))
    merged = []
    gap = max(0.0, float(merge_gap))
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [(round(start, 3), round(end, 3)) for start, end in merged]


def interlude_fade_expression(windows, fade_seconds=1.2):
    """FFmpeg expression for a smooth 0..1 envelope over gap windows."""
    ramps = []
    fade = max(0.1, float(fade_seconds))
    for start, end in windows:
        ramps.append(
            f"between(T,{start:.3f},{end:.3f})*"
            f"min(1,min(max(0,(T-{start:.3f})/{fade:.3f}),"
            f"max(0,({end:.3f}-T)/{fade:.3f})))"
        )
    if not ramps:
        return "0"
    expression = ramps[0]
    for ramp in ramps[1:]:
        expression = f"max({expression},{ramp})"
    return expression


def lyric_focus_fade_expression(windows, fade_seconds=0.45):
    """0..1 envelope: dark before the first syllable, fade after the line."""
    ramps = []
    fade = max(0.1, float(fade_seconds))
    for start, end in windows:
        before = max(0.0, start - fade)
        after = end + fade
        ramps.append(
            f"between(T,{before:.3f},{after:.3f})*"
            f"min(1,min(max(0,(T-{before:.3f})/{fade:.3f}),"
            f"max(0,({after:.3f}-T)/{fade:.3f})))"
        )
    if not ramps:
        return "0"
    expression = ramps[0]
    for ramp in ramps[1:]:
        expression = f"max({expression},{ramp})"
    return expression


def render_lyric_video(ff, mp3, bg_png, ass_path, out_mp4, height=1080, fps=30,
                       vis="bars", crf=21, drawtext_chain=None, accent="0x22d3a6",
                       shimmer=True, interlude_mode=True, lyric_focus_band=True,
                       focus_bg_png=None, focus_opacity=0.90,
                       progress_callback=None, process_callback=None):
    w, h = int(height * 16 / 9), int(height)
    have = ffmpeg_filters(ff)

    def ok(name):
        return (not have) or (name in have)

    if ass_path and not ok("subtitles"):
        ass_path = None            # caller should have supplied drawtext_chain
    if not ass_path and not drawtext_chain:
        pass                       # visualiser-only video; still valid

    use_focus_art = bool(focus_bg_png and Path(focus_bg_png).exists())
    audio_input = 2 if use_focus_art else 1

    # Keep the artwork composition fixed. Optional movement is confined to
    # highlights, avoiding the crop and softness of the former zoompan.
    chains = [f"[0:v]scale={w}:{h},format=yuv420p,fps={fps}[bg]"]
    last = "bg"
    shimmer_filters = ("noise", "lutyuv", "drawbox", "gblur", "blend")
    if shimmer and all(ok(name) for name in shimmer_filters):
        # Build the twinkle mask at quarter resolution so individual points
        # become visible glints instead of imperceptible one-pixel video noise.
        # Only already-bright pixels can enter the mask; sampling it at 8 fps
        # makes each sparkle linger briefly instead of buzzing every frame.
        # Lyrics and the visualizer are composited later and remain crisp.
        chains.append(f"[{last}]split=2[still][spark_src]")
        chains.append(
            f"[spark_src]scale={max(160, w // 4)}:{max(90, h // 4)},"
            "noise=alls=55:allf=t+u,"
            "lutyuv=y='if(gt(val,215),255,0)':u=128:v=128,"
            "drawbox=x=0:y=ih*0.56:w=iw:h=ih*0.22:color=black:t=fill,"
            f"fps=8,scale={w}:{h}:flags=neighbor,gblur=sigma=2.2[spark]"
        )
        # Blend luminance only. Screening neutral U/V planes would tint the
        # entire picture magenta instead of merely brightening the glints.
        chains.append(
            "[still][spark]blend=c0_expr='min(255,A+B*0.70)':"
            "c1_expr='A':c2_expr='A'[shimmer]"
        )
        last = "shimmer"
    interludes = ass_interlude_windows(ass_path) if interlude_mode else []
    interlude_filters = ("gblur", "geq", "tmix", "color", "alphamerge",
                         "colorchannelmixer", "overlay")
    if interludes and all(ok(name) for name in interlude_filters):
        # Sparse, large warm-gold glints appear only in genuine gaps between
        # lyric events. Restricting their seeds to dark pixels gives the gold
        # contrast and naturally favors the calm lyric band while it is empty.
        # A 1 fps refresh plus temporal mixing keeps movement slow and smooth.
        enabled = "+".join(f"between(t,{start:.3f},{end:.3f})"
                           for start, end in interludes)
        fade = interlude_fade_expression(interludes)
        chains.append(f"[{last}]split=2[interlude_still][interlude_src]")
        chains.append(
            f"[interlude_src]scale={max(160, w // 12)}:{max(90, h // 12)},"
            "format=gray,fps=1,"
            "geq=lum='if(lt(lum(X,Y),112)*"
            "lt(abs(mod(sin(X*12.9898+Y*78.233+N*37.719)"
            "*43758.5453,1)),0.006),255,0)',"
            "tmix=frames=3:weights='1 1 1':scale=0.65,gblur=sigma=0.7,"
            f"fps={fps},geq=lum='clip(lum(X,Y)*({fade}),0,255)',"
            f"scale={w}:{h}:flags=bicubic,format=gray[interlude_mask]"
        )
        chains.append(
            f"color=c=0xFFD166:s={w}x{h}:r={fps},format=rgba[interlude_gold]"
        )
        chains.append(
            "[interlude_gold][interlude_mask]alphamerge,"
            "colorchannelmixer=aa=0.96[interlude_glints]"
        )
        chains.append(
            f"[interlude_still][interlude_glints]overlay=shortest=1:"
            f"enable='{enabled}'[interlude]"
        )
        last = "interlude"
    lyric_windows = ass_lyric_windows(ass_path) if lyric_focus_band else []
    focus_filters = (("crop", "geq", "alphamerge", "overlay") +
                     (() if use_focus_art else ("eq",)))
    if lyric_windows and all(ok(name) for name in focus_filters):
        # The source is either GPT Image's exact masked edit or, if that paid
        # edit was unavailable, a locally darkened copy of the original art.
        # Only the 64%-79% strip is composited; 20px feathering avoids a hard
        # panel edge and the temporal envelope is the inverse of interludes.
        band_y = round(h * 0.64)
        band_h = max(40, round(h * 0.15))
        feather = max(4, round(h * 20 / 1080))
        fade = lyric_focus_fade_expression(lyric_windows)
        source = "1:v" if use_focus_art else last
        if use_focus_art:
            chains.append(f"[{source}]scale={w}:{h},format=yuv420p,fps={fps}[focus_full]")
            source = "focus_full"
        else:
            chains.append(f"[{last}]split=2[focus_still][focus_full]")
            last = "focus_still"
            source = "focus_full"
        darken = "" if use_focus_art else ",eq=brightness=-0.34:saturation=0.72"
        chains.append(
            f"[{source}]crop={w}:{band_h}:0:{band_y}{darken},format=rgb24[focus_rgb]"
        )
        spatial = (f"min(1,min(Y/{feather:.1f},"
                   f"({band_h - 1}-Y)/{feather:.1f}))")
        alpha = max(0.0, min(1.0, float(focus_opacity)))
        chains.append(
            f"color=white:s={w}x{band_h}:r={fps},format=gray,"
            f"geq=lum='clip(255*{alpha:.3f}*({spatial})*({fade}),0,255)'"
            "[focus_alpha]"
        )
        chains.append("[focus_rgb][focus_alpha]alphamerge[focus_strip]")
        chains.append(
            f"[{last}][focus_strip]overlay=x=0:y={band_y}:shortest=1[focused]"
        )
        last = "focused"
    vh = max(60, int(h * 0.115))
    if vis == "bars" and not ok("showfreqs"):
        vis = "wave" if ok("showwaves") else "off"
    elif vis == "wave" and not ok("showwaves"):
        vis = "bars" if ok("showfreqs") else "off"
    vc = visualizer_chain(vis, w, vh, accent=accent)
    if vc:
        chains.append(f"[{audio_input}:a]asplit=2[aout][avis]")
        chains.append(f"[avis]{vc}[wv]")
        chains.append(f"[{last}][wv]overlay=x=0:y=H-{vh + int(h * 0.03)}:shortest=0[v1]")
        last, amap = "v1", "[aout]"
    else:
        amap = f"{audio_input}:a"
    if ass_path:
        chains.append(f"[{last}]subtitles='{ff_path(ass_path)}'[vout]")
        last = "vout"
    elif drawtext_chain:
        chains.append(f"[{last}]{drawtext_chain}[vout]")
        last = "vout"
    dur = audio_duration(ff, mp3)
    args = ["-loop", "1", "-i", str(bg_png)]
    if use_focus_art:
        args += ["-loop", "1", "-i", str(focus_bg_png)]
    args += ["-i", str(mp3),
            "-filter_complex", ";".join(chains),
            "-map", f"[{last}]", "-map", amap,
            # A near-static frame compresses enormously better with
            # tune=stillimage. Shimmer is deliberately sparse and subtle.
            "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
            "-crf", str(int(crf)), "-maxrate", "2500k", "-bufsize", "5000k",
            "-pix_fmt", "yuv420p", "-r", str(int(fps)),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    # Explicit -t, else the looped still image encodes forever.
    args += (["-t", f"{dur:.3f}"] if dur else ["-shortest"])
    # The recovery file deliberately ends in .part so folder watchers cannot
    # mistake it for a finished MP4; specify the muxer instead of inferring it.
    args += ["-f", "mp4", str(out_mp4)]
    run_ffmpeg(ff, args, "video render", progress_callback=progress_callback,
               duration=dur, process_callback=process_callback)
    return out_mp4


# Song, image, and encoder work share the configured external-work budget.
# Gate-paused jobs never acquire it.
VIDEO_SLOTS = _slots


def run_video_job(job_id, track, recovering=False):
    """track: the dict we stored when the song was downloaded."""
    def log(m):
        set_job(job_id, message=m)
        print(f"[{job_id[:8]}] {m}")

    acquired = False
    scratch = None
    out = None
    part = None
    try:
        if not VIDEO_SLOTS.acquire(blocking=False):
            set_job(job_id, status="queued", message="waiting for the video renderer")
            VIDEO_SLOTS.acquire()
        acquired = True
        ff = find_ffmpeg()
        if not ff:
            raise RuntimeError("ffmpeg not found. Install it with:  brew install ffmpeg-full")
        mp3 = Path(track["file"])
        if not mp3.exists():
            raise RuntimeError("the audio file is missing - was it moved?")
        folder = mp3.parent
        expected_duration = audio_duration(ff, mp3)
        with JOBS_LOCK:
            saved_job = dict(JOBS.get(job_id) or {})
        pipeline = bool(saved_job.get("pipeline"))
        if pipeline:
            selected_art = str(saved_job.get("video_image_file") or "")
            render_art = str(track.get("pipeline_image") or "")
            if not selected_art or render_art != selected_art:
                raise RuntimeError("video render lost its selected gallery image; no substitute was generated")
            if not Path(selected_art).is_file():
                raise RuntimeError("the selected gallery image is missing; no substitute was generated")
        saved_output = saved_job.get("output_path") if recovering else ""
        saved_art = saved_job.get("art_path") if recovering else ""
        saved_focus_art = saved_job.get("focus_art_path") if recovering else ""
        saved_part = video_part_path(saved_output) if saved_output else None
        if (saved_part and completed_video_matches(
                ff, saved_part, expected_duration)):
            saved_part.replace(saved_output)
        if saved_output and completed_video_matches(ff, saved_output, expected_duration):
            out = Path(saved_output)
            size = out.stat().st_size / 1e6
            set_job(job_id, status="done", phase="done", progress=100,
                    message=f"recovered completed video - {size:.1f} MB",
                    tracks=[{"file": str(out), "name": out.name, "video": True}],
                    folder=str(folder))
            with JOBS_LOCK:
                JOB_FORMS.pop(job_id, None)
                _save_jobs_locked()
            return
        if recovering:
            if (saved_part and saved_job.get("encoder_pid") and
                    terminate_interrupted_encoder(
                        saved_job.get("encoder_pid"), saved_part)):
                set_job(job_id, encoder_pid=None,
                        message="stopped interrupted encoder; restarting safely")
            cleanup_video_scratch(folder, job_id)
        scratch = Path(tempfile.mkdtemp(prefix=f".suno-{job_id[:8]}-", dir=str(folder)))
        title = track.get("song_title") or mp3.stem
        # "Neon Rain - take 2" is a filename, not a song title.
        title = re.sub(r"\s*[-–—]\s*take\s*\d+\s*$", "", title, flags=re.I).strip() or mp3.stem
        # The provider's downloaded filename is an implementation detail. For
        # a pipeline video, name the deliverable from the currently approved
        # request fields so retries and provider title changes cannot leak into
        # the final MP4 name.
        fields = dict(saved_job.get("current_fields") or JOB_FORMS.get(job_id) or {})
        video_basename = safe_name(compose_basename(
            fields.get("tagline") or track.get("tagline") or "",
            fields.get("title") or title)) if pipeline else mp3.stem
        set_job(job_id, status="running", phase="alignment", progress=None,
                message="preparing lyric alignment")

        # 1. word timings, from cache if we already have them
        words_path = mp3.with_suffix(".words.json")
        aligned, lyrics_text = [], ""
        if words_path.exists():
            try:
                cached = json.loads(words_path.read_text())
                aligned = cached.get("alignedWords") or []
                lyrics_text = cached.get("lyrics") or ""
                log(f"using cached word timings ({len(aligned)} words)")
            except Exception:
                aligned = []
        if not lyrics_text:
            # older songs: recover the lyrics from the .txt sidecar
            for cand in sorted(folder.glob("*.txt")):
                try:
                    body = cand.read_text(encoding="utf-8")
                    if "-" * 20 in body:
                        lyrics_text = body.split("-" * 20, 1)[1].strip()
                        break
                except Exception:
                    pass
        if not aligned and track.get("suno_id") and track.get("task_id"):
            log("fetching word-level lyric timings")
            prov = make_provider(CONFIG, track.get("provider"))
            if not hasattr(prov, "timestamps"):
                raise RuntimeError(f"{prov.label} has no lyric-alignment endpoint")
            data = prov.timestamps(track["task_id"], track["suno_id"])
            data["lyrics"] = lyrics_text
            words_path.write_text(json.dumps(data))
            aligned = data["alignedWords"]
            log(f"got {len(aligned)} aligned words")

        render_lyrics_text = lyrics_text
        if CONFIG.get("lyric_aligner") == "stable-ts-hybrid" and lyrics_text:
            try:
                hybrid = build_stable_ts_hybrid(
                    mp3, lyrics_text, aligned, ff, scratch_dir=scratch, log=log)
                aligned = hybrid["alignedWords"]
                # Align only against accepted authored lines. This preserves
                # long exact line breaks without forcing a deliberately hidden
                # line back into the output.
                render_lyrics_text = hybrid.get("safe_lyrics") or ""
                if hybrid.get("warnings"):
                    set_job(job_id, note="; ".join(hybrid["warnings"][:3]))
            except Exception as error:
                log(f"local stable-ts unavailable ({error}) - using section aligner")
                set_job(job_id, note=f"stable-ts fallback: {str(error)[:180]}")

        # 2. background still
        set_job(job_id, phase="artwork", progress=None)
        log("building the background image")
        bg = folder / f"{mp3.stem} - background.png"
        cover = track.get("pipeline_image") if pipeline else None
        focus_cover = None
        art = bool(cover)
        skip_drawn_title = bool(cover)
        if not pipeline and (CONFIG.get("bg_source") or "gradient") == "ai":
            key = (CONFIG.get("openai_key") or "").strip()
            if not art_available():
                log("AI artwork is disabled for this session (repeated provider "
                    "failures) - using the gradient")
                set_job(job_id, note="artwork disabled after repeated failures")
                key = ""
            if not key:
                log("AI background needs an OpenAI key - using the gradient")
                set_job(job_id, note="no OpenAI key saved - gradient background used")
            else:
                try:
                    art_title = bool(CONFIG.get("art_title"))
                    art_path = folder / f"{mp3.stem} - art.png"
                    if saved_art == str(art_path) and valid_image(ff, art_path):
                        cover = art_path
                        log("reusing completed background artwork")
                    else:
                        log("generating background art (this adds ~30-60s)")
                        cover = generate_background_image(
                            key, title, track.get("tags") or track.get("style") or "",
                            art_path, draw_title=art_title,
                            tagline=track.get("tagline") or "")
                    set_job(job_id, art_path=str(cover))
                    art = True
                    skip_drawn_title = art_title
                    log("background art ready")
                    if CONFIG.get("lyric_focus_band", True):
                        try:
                            focus_path = folder / f"{mp3.stem} - lyric focus art.png"
                            if (saved_focus_art == str(focus_path) and
                                    valid_image(ff, focus_path)):
                                focus_cover = focus_path
                                log("reusing completed lyric focus artwork")
                            else:
                                log("creating lyric focus band (one additional image edit)")
                                focus_cover = openai_lyric_band_edit(
                                    key, cover, focus_path, ff,
                                    model=CONFIG.get("openai_image_model") or "gpt-image-2")
                            set_job(job_id, focus_art_path=str(focus_cover))
                            log("lyric focus artwork ready")
                        except Exception as focus_error:
                            # This enhancement must never turn a usable song and
                            # background into a failed video render.
                            focus_cover = None
                            log(f"AI lyric focus edit unavailable ({focus_error}) - "
                                "using the local focus band")
                except Exception as e:
                    note_art_failure(e)
                    note_openai_exhausted(e)
                    log(f"art generation failed ({e}) - using gradient")
                    set_job(job_id, note=f"artwork failed: {str(e)[:150]}")
                    cover = None
        # Title only. The style string is a prompt for Suno, not a credit -
        # nobody wants "70s soul, horn section, 110bpm" on screen.
        subtitle = ""
        bg, drew_title = make_background(ff, bg, "" if skip_drawn_title else title,
                                         subtitle, cover=cover,
                                         height=int(CONFIG.get("video_height") or 1080),
                                         art=art, scratch_dir=scratch)
        focus_bg = None
        if focus_cover:
            try:
                focus_bg = folder / f"{mp3.stem} - lyric focus background.png"
                focus_bg, _ = make_background(
                    ff, focus_bg, "", "", cover=focus_cover,
                    height=int(CONFIG.get("video_height") or 1080), art=True,
                    scratch_dir=scratch)
            except Exception as focus_error:
                focus_bg = None
                log(f"could not prepare AI lyric focus artwork ({focus_error}) - "
                    "using the local focus band")
        if skip_drawn_title:
            drew_title = True          # the artwork carries it
        if not drew_title:
            log("no drawtext filter - putting the title on via subtitles instead")

        # 3. lyrics: libass gives per-word karaoke; drawtext is the fallback
        set_job(job_id, phase="subtitles", progress=None,
                message="preparing timed subtitles")
        vh = int(CONFIG.get("video_height") or 1080)
        have = ffmpeg_filters(ff)
        ass_path, dt_chain, dt_files = None, None, []
        if aligned:
            if "subtitles" in have or not have:
                ass_text, nlines = build_karaoke_ass(
                    aligned, font=ass_font_name(), lyrics_text=render_lyrics_text,
                    banner=None if (drew_title or not title.strip())
                           else (title, subtitle))
                if CONFIG.get("lyric_aligner") == "stable-ts-hybrid":
                    log("line breaks and timings taken from local stable-ts hybrid")
                elif render_lyrics_text:
                    log("line breaks taken from the submitted lyrics")
                ass_path = folder / f"{mp3.stem}.ass"
                ass_path.write_text(ass_text, encoding="utf-8")
                log(f"timed {nlines} lyric lines (word-level karaoke)")
            elif "drawtext" in have:
                dt_chain, dt_files, nlines = build_drawtext_lyrics(
                    aligned, int(vh * 16 / 9), vh, find_font(), scratch,
                    lyrics_text=render_lyrics_text)
                log(f"timed {nlines} lyric lines (no libass - line-level, "
                    f"no word sweep)")
            else:
                raise RuntimeError(
                    "This ffmpeg has neither 'subtitles' (libass) nor 'drawtext' "
                    "(freetype), so no lyrics can be drawn. Reinstall with:  "
                    "brew install ffmpeg-full")
        else:
            log("no lyric timings - rendering a visualiser-only video")

        # 4. render
        set_job(job_id, phase="render", progress=0,
                message="rendering video — 0%")
        # A standalone video can live in a watched folder. Pipeline videos
        # must stay in staging until the recipient is checked at final approval.
        vdir = (CONFIG.get("video_dir") or "").strip()
        if vdir and not pipeline:
            vpath = Path(os.path.expanduser(vdir))
            try:
                vpath.mkdir(parents=True, exist_ok=True)
                probe = vpath / ".suno_write_test"
                probe.write_text("x")
                probe.unlink()
            except Exception as e:
                log(f"video folder unusable ({e}) - saving beside the audio instead")
                vpath = folder
        else:
            vpath = folder
        # Legacy standalone renders can still use a recipient-named watch
        # folder. Pipeline publication applies this routing only after approval.
        who = (track.get("recipient") or "").strip().lower()
        if who and vdir and not pipeline:
            safe = delivery_folder(who)
            if safe:
                try:
                    (vpath / safe).mkdir(parents=True, exist_ok=True)
                    vpath = vpath / safe
                    log(f"video will be filed under {safe}/ for notification")
                except Exception as e:
                    log(f"could not create {safe}/ ({e}) - using the parent folder")
        if recovering and saved_output:
            out = Path(saved_output)
            out.parent.mkdir(parents=True, exist_ok=True)
        else:
            out = reserve_unique_path(vpath / f"{video_basename}.mp4")
            # The final filename must never expose a zero-byte or partial MP4.
            # VIDEO_SLOTS serializes allocation; the .part file is used until
            # an atomic rename publishes a fully encoded video.
            out.unlink()
        set_job(job_id, output_path=str(out))
        part = video_part_path(out)
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        try:
            vis_mode = CONFIG.get("visualizer") or "bars"
            accent = palette_for(title)[2]
            if vis_mode != "off":
                accent = art_accent_color(ff, bg, accent)
                log(f"visualizer colour sampled from artwork: {accent}")
            render_started = time.monotonic()
            progress_state = {"percent": -1, "updated": 0.0}

            def report_progress(fraction):
                percent = max(0, min(100, int(float(fraction) * 100)))
                now = time.monotonic()
                if percent < 100 and (percent <= progress_state["percent"] or
                                      now - progress_state["updated"] < 0.75):
                    return
                elapsed = max(0.0, now - render_started)
                eta = (elapsed * (1.0 - fraction) / fraction
                       if fraction > 0.01 else None)
                remaining = ""
                if eta is not None and percent < 100:
                    minutes, seconds = divmod(max(0, int(round(eta))), 60)
                    remaining = (f" · about {minutes}m {seconds:02d}s remaining"
                                 if minutes else f" · about {seconds}s remaining")
                progress_state.update(percent=percent, updated=now)
                set_job(job_id, phase="render", progress=percent,
                        message=f"rendering video — {percent}%{remaining}")

            def register_encoder(pid):
                set_job(job_id, encoder_pid=int(pid))

            render_lyric_video(ff, mp3, bg, ass_path, part,
                               accent=accent,
                               height=vh,
                               fps=int(CONFIG.get("video_fps") or 30),
                               vis=vis_mode,
                               shimmer=bool(CONFIG.get("shimmer", True)),
                               interlude_mode=bool(CONFIG.get("interlude_mode", True)),
                               lyric_focus_band=bool(CONFIG.get("lyric_focus_band", True)),
                               focus_bg_png=focus_bg,
                               focus_opacity=0.90,
                               crf=int(CONFIG.get("video_crf") or 21),
                               drawtext_chain=dt_chain,
                               progress_callback=report_progress,
                               process_callback=register_encoder)
            part.replace(out)
        finally:
            for f in dt_files:
                try:
                    f.unlink()
                except OSError:
                    pass
        if CONFIG.get("copy_path"):
            try:
                subprocess.run(["pbcopy"], input=str(out), text=True, timeout=5)
                log("path copied to the clipboard")
            except Exception:
                pass
        size = out.stat().st_size / 1e6
        if pipeline:
            paused = bool(CONFIG.get("gate_video"))
            set_job(job_id, status=("paused_video" if paused else "running"), stage="video",
                    phase="done", progress=100, encoder_pid=None, video_path=str(out),
                    message=("video ready for approval" if paused else "publishing video"),
                    tracks=[{"file": str(out), "name": out.name, "video": True}], folder=str(folder))
            if not paused:
                finalize_pipeline_job(job_id)
        else:
            set_job(job_id, status="done", phase="done", progress=100,
                    encoder_pid=None,
                    message=f"done - {size:.1f} MB",
                    tracks=[{"file": str(out), "name": out.name, "video": True}],
                    folder=str(folder))
            with JOBS_LOCK:
                JOB_FORMS.pop(job_id, None)
                _save_jobs_locked()
        log(f"finished -> {out.name}")
    except Exception as e:
        if part:
            try:
                part.unlink()
            except OSError:
                pass
        if out:
            try:
                out.unlink()
            except OSError:
                pass
        if JOB_CANCELS.setdefault(job_id, threading.Event()).is_set():
            set_job(job_id, status="interrupted", encoder_pid=None,
                    message="video rendering interrupted; restart when ready")
        else:
            set_job(job_id, status="error", encoder_pid=None, message=str(e))
            print(f"[{job_id[:8]}] VIDEO ERROR: {e}")
    finally:
        # drawtext scratch files - remove them even if the render blew up
        try:
            if scratch and scratch.is_dir():
                for tmp in scratch.iterdir():
                    if tmp.is_file():
                        tmp.unlink()
                scratch.rmdir()
        except Exception:
            pass
        if acquired:
            VIDEO_SLOTS.release()


def start_video_job(track, source="manual"):
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "title": (track.get("song_title") or Path(track["file"]).stem) + "  (video)",
            "style": "", "status": "queued", "message": "queued",
            "created": time.time(), "created_str": datetime.now().strftime("%H:%M"),
            "tracks": [], "folder": "", "task_id": "", "source": source, "kind": "video",
            "note": "", "phase": "queued", "progress": None,
        }
        JOB_FORMS[job_id] = {"track": dict(track)}
        _save_jobs_locked()
    threading.Thread(target=run_video_job, args=(job_id, track), daemon=True).start()
    return job_id


# --------------------------------------------------------------------------
# running-dry alerts
# --------------------------------------------------------------------------

ALERTED = set()          # one warning per condition per session


def todoist_task(content, note=""):
    token = (CONFIG.get("todoist_token") or "").strip()
    if not token:
        return False
    body = {"content": content}
    if note:
        body["description"] = note
    if (CONFIG.get("todoist_project") or "").strip():
        body["project_id"] = CONFIG["todoist_project"].strip()
    try:
        api_json("POST", "https://api.todoist.com/rest/v2/tasks", token, body, timeout=20)
        print(f"[alert] added to Todoist: {content}")
        return True
    except Exception as e:
        print(f"[alert] Todoist failed: {e}")
        return False


def raise_alert(key, headline, detail=""):
    """Warn once per session: log, note it in the UI, and file a Todoist task."""
    if key in ALERTED:
        return
    ALERTED.add(key)
    print(f"[alert] {headline} {detail}")
    ALERTS.append({"headline": headline, "detail": detail,
                   "when": datetime.now().strftime("%H:%M")})
    todoist_task(headline, detail)


def kie_credit_balance():
    key = (CONFIG.get("kie_key") or "").strip()
    if not key:
        return None
    try:
        r = api_json("GET", "https://api.kie.ai/api/v1/chat/credit", key, timeout=20)
        d = r.get("data")
        return float(d) if isinstance(d, (int, float)) else float((d or {}).get("credit"))
    except Exception as e:
        print(f"[alert] could not read kie.ai credit: {e}")
        return None


def check_balances():
    """Poll what CAN be polled. OpenAI has no public balance endpoint, so that
    side is caught reactively from its insufficient_quota error instead."""
    if not CONFIG.get("alerts_enabled"):
        return
    bal = kie_credit_balance()
    if bal is None:
        return
    try:
        floor = float(CONFIG.get("kie_low_credits") or 100)
    except (TypeError, ValueError):
        floor = 100
    print(f"[alert] kie.ai balance {bal:g} (warn under {floor:g})")
    if bal <= floor:
        raise_alert("kie_low",
                    f"Suno Studio: kie.ai credits low ({bal:g})",
                    f"Songs stop generating at zero. Top up at kie.ai. "
                    f"Threshold is {floor:g}.")


def note_openai_exhausted(err):
    """OpenAI publishes no balance endpoint, but insufficient_quota is
    unambiguous - treat the first one as the alert."""
    t = str(err).lower()
    if "insufficient_quota" in t or "exceeded your current quota" in t or "billing" in t:
        raise_alert("openai_quota",
                    "Suno Studio: OpenAI credit exhausted",
                    "Lyric-video artwork is falling back to a plain gradient. "
                    "Add credit at platform.openai.com/settings/organization/billing.")


# --------------------------------------------------------------------------
# gmail watcher  (read-only: we never mark, move, or delete anything)
# --------------------------------------------------------------------------

ALERTS = []                     # low-balance warnings shown in the UI
INBOX_LOCK = threading.Lock()
WATCH = {"state": "off", "message": "not running", "last_check": 0}


def load_inbox():
    try:
        values = json.loads(INBOX_PATH.read_text(encoding="utf-8"))
        return {item["id"]: item for item in values
                if isinstance(item, dict) and item.get("id")}
    except Exception:
        return {}


INBOX = load_inbox()            # pending requests survive app restarts


def _save_inbox_locked():
    try:
        atomic_write_json(INBOX_PATH, list(INBOX.values()), mode=0o600)
    except Exception as e:
        print(f"[watch] could not save approval inbox: {e}")

# Header lines Rovo can put at the top of the email body.
FIELD_ALIASES = {
    "style": "style", "genre": "style", "tags": "style",
    "title": "title", "song": "title", "name": "title",
    "model": "model", "version": "model",
    "instrumental": "instrumental",
    "vocal": "vocalGender", "vocals": "vocalGender", "voice": "vocalGender",
    "exclude": "negativeTags", "avoid": "negativeTags", "negative": "negativeTags",
    "lyrics": "lyrics", "words": "lyrics", "lyric": "lyrics",
    "sprint": "tagline", "tagline": "tagline", "subtitle": "tagline",
    "infographic": "infographic", "screen": "infographic",
    "email": "recipient", "notify": "recipient", "requester": "recipient",
    "to": "recipient", "reply": "recipient", "replyto": "recipient",
    "team": "tagline", "project": "tagline",
}
TRUEISH = {"yes", "y", "true", "1", "on", "instrumental"}


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def first_email(text):
    """Pull one address out of a field that might be 'Matt <m@x.com>' or a list."""
    m = EMAIL_RE.search(text or "")
    return m.group(0).lower() if m else ""


def compose_basename(sprint, title):
    """
    "<sprint> - <title>", but only when that actually adds information.
    A subject line like "Learning Path - Learning Path 26.3.2" plus a sprint of
    "Learning Path 26.3.2" used to produce a filename that said it three times.
    """
    sprint = (sprint or "").strip(" -")
    title = (title or "").strip(" -")
    if not sprint:
        return title or "Untitled"
    if not title or title.lower() in ("untitled", "song"):
        return sprint
    a, b = _norm(sprint), _norm(title)
    if a == b or a in b or b in a:
        return title if len(b) >= len(a) else sprint
    return f"{sprint} - {title}"


def clean_subject(subject):
    """Strip routing tags like [SPRINT SONG] / [SUNO] from an email subject,
    and collapse 'Name - Name' duplication."""
    s = re.sub(r"^\s*(?:\[[^\]]{1,30}\]\s*)+", "", subject or "").strip()
    s = re.sub(r"^\s*(?:re|fwd)\s*:\s*", "", s, flags=re.I).strip()
    # "Learning Path - Learning Path 26.3.2": collapse when one half repeats
    # or merely prefixes the other, not only on an exact match.
    m = re.match(r"^(.{3,}?)\s*[-–—]\s*(.+)$", s)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        na, nb = _norm(a), _norm(b)
        if na and nb and (na == nb or nb.startswith(na) or na.startswith(nb)):
            s = b if len(nb) >= len(na) else a
    return s.strip(" -–—")


SEEN_CAP = 1000
FIRST_RUN_DAYS = 2      # on a virgin install, ignore mail older than this


def load_seen():
    """An insertion-ordered dict, not a set: order is what makes trimming safe."""
    try:
        return dict.fromkeys(json.loads(SEEN_PATH.read_text()), 1)
    except Exception:
        return {}


def save_seen(seen):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Keep the NEWEST ids. Sorting by message-id would evict at random,
        # which silently resurrects old mail as "new".
        atomic_write_json(SEEN_PATH, list(seen)[-SEEN_CAP:], mode=0o600)
    except Exception as e:
        print(f"[watch] could not save seen list: {e}")


def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|h[1-6])>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return s


def decode_header(raw):
    if not raw:
        return ""
    out = []
    for part, enc in email.header.decode_header(raw):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", "replace"))
        else:
            out.append(part)
    return "".join(out).strip()


def body_text(msg):
    """Prefer text/plain; fall back to de-tagged text/html."""
    plain, html_body = None, None
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html_body is None:
            html_body = text
    if plain and plain.strip():
        return plain
    return strip_html(html_body or "")


# Section-block styles that LLMs reach for unprompted:
#   ===STYLE=== / --- STYLE --- / ## STYLE / **STYLE**
# The value is on the FOLLOWING line(s), not the same line.
SECTION_RE = re.compile(
    r"^\s*(?:={2,}\s*([A-Za-z ]{3,20}?)\s*={2,}"
    r"|-{2,}\s*([A-Za-z ]{3,20}?)\s*-{2,}"
    r"|#{1,4}\s*([A-Za-z ]{3,20}?)\s*#*"
    r"|\*{2}\s*([A-Za-z ]{3,20}?)\s*:?\s*\*{2})\s*:?\s*$")


def marker_text(line):
    """Remove invisible mail/editor controls that can precede ===HEADERS===.

    Google/Atlassian editors sometimes insert zero-width joiners, direction
    marks, or a BOM at the start of copied text. Python's whitespace regex
    does not match these format controls, so an otherwise valid TITLE marker
    could be missed while every later marker still parsed normally.
    """
    return re.sub(r"^(?:\s|[\u200b-\u200f\u2060\ufeff])+", "", line or "")


def split_sections(text):
    """{field: value} if the body uses section blocks, else None."""
    hits, cur, buf = {}, None, []
    found_any = False
    for line in text.split("\n"):
        m = SECTION_RE.match(marker_text(line))
        name = next((g for g in (m.groups() if m else ()) if g), None) if m else None
        key = FIELD_ALIASES.get((name or "").strip().lower()) if name else None
        if name and key:
            if cur:
                hits[cur] = "\n".join(buf).strip()
            cur, buf, found_any = key, [], True
            continue
        if name and not key and cur:
            # An unrecognised block ends the current one rather than absorbing it.
            hits[cur] = "\n".join(buf).strip()
            cur, buf = None, []
            continue
        if cur:
            buf.append(line)
    if cur:
        hits[cur] = "\n".join(buf).strip()
    if not found_any:
        return None
    # No explicit lyrics block? Then the lyrics were probably left dangling at
    # the end of another section. Split it at the first [Verse]-style tag.
    if not hits.get("lyrics"):
        for k, v in list(hits.items()):
            if k == "lyrics":
                continue
            rows = v.split("\n")
            at = next((i for i, r in enumerate(rows) if re.match(r"^\s*\[[^\]]+\]\s*$", r)), None)
            if at is not None:
                hits[k] = "\n".join(rows[:at]).strip()
                hits["lyrics"] = "\n".join(rows[at:]).strip()
                break
    return hits


def scrub_scaffolding(s):
    """Drop stray ===HEADER=== lines so they never get sung."""
    return "\n".join(l for l in (s or "").split("\n")
                     if not SECTION_RE.match(marker_text(l))).strip()


def undo_quoted_printable(text):
    """Some senders deliver quoted-printable without a matching
    Content-Transfer-Encoding header, so '=' arrives as '=3D' and every
    ===SECTION=== marker is destroyed."""
    # Only step in when the markers are actually broken. Running quopri over a
    # normal body is destructive: it eats "===" as escape sequences.
    if "=3D" not in text or "===" in text:
        return text
    try:
        import quopri
        fixed = quopri.decodestring(text.encode("utf-8", "replace")).decode("utf-8", "replace")
        # "=3D=3D=3D" and "===" contain the same number of "=" characters,
        # so compare on markers actually appearing, not on counts.
        if "===" in fixed or fixed.count("=3D") < text.count("=3D"):
            return fixed
        return text
    except Exception:
        return text


def parse_request(subject, body, default_style=""):
    """
    Turn an email into a generation form. Two layouts are understood:

      Style: dreamy synthpop        |   ===STYLE===
      ---                           |   dreamy synthpop
      [Verse]                       |   ===LYRICS===
      ...                           |   [Verse] ...

    Headers are optional and order-free.
    """
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    text = undo_quoted_printable(text)
    # Drop quoted replies and common signature delimiters.
    text = re.split(r"\n-- \n|\n_{5,}\n|\nOn .{0,80} wrote:\n", text)[0]

    # Section-block layout wins if present - it's unambiguous.
    sect = split_sections(text)
    if sect:
        instrumental = (sect.get("instrumental", "").strip().lower() in TRUEISH)
        gender = sect.get("vocalGender", "").strip().lower()
        title = (sect.get("title") or "").strip() or clean_subject(subject)
        if not title:
            title = (sect.get("tagline") or "").strip()
        return {
            "title": title or "Untitled",
            "style": scrub_scaffolding(sect.get("style", "")).replace("\n", ", ").strip(", ")
                     or default_style,
            "lyrics": "" if instrumental else scrub_scaffolding(sect.get("lyrics", "")),
            "model": (sect.get("model") or "").strip(),
            "instrumental": instrumental,
            "negativeTags": (sect.get("negativeTags") or "").strip(),
            "tagline": (sect.get("tagline") or "").strip(),
            "infographic": scrub_scaffolding(sect.get("infographic", "")),
            "recipient": first_email(sect.get("recipient") or ""),
            "vocalGender": "f" if gender.startswith("f") else "m" if gender.startswith("m") else "",
            "styleWeight": None,
            "weirdnessConstraint": None,
        }

    fields, lyric_lines, in_lyrics = {}, [], False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not in_lyrics:
            if re.match(r"^\s*-{3,}\s*$", line):
                in_lyrics = True
                continue
            m = re.match(r"^\s*([A-Za-z]{3,14})\s*:\s*(.*)$", line)
            # A structure tag like "[Verse]" means the lyrics have begun.
            if m and not line.lstrip().startswith("["):
                key = FIELD_ALIASES.get(m.group(1).strip().lower())
                if key:
                    fields[key] = m.group(2).strip()
                    continue
            if not line.strip():
                continue
            in_lyrics = True
        lyric_lines.append(line)

    lyrics = scrub_scaffolding("\n".join(lyric_lines))

    title = fields.get("title") or clean_subject(subject)
    instrumental = (fields.get("instrumental", "").strip().lower() in TRUEISH)
    gender = fields.get("vocalGender", "").strip().lower()
    gender = "f" if gender.startswith("f") else "m" if gender.startswith("m") else ""

    return {
        "title": title or "Untitled",
        "style": fields.get("style") or default_style,
        "lyrics": "" if instrumental else lyrics,
        "model": fields.get("model", "").strip(),
        "instrumental": instrumental,
        "negativeTags": fields.get("negativeTags", ""),
        "tagline": fields.get("tagline", "").strip(),
        "infographic": fields.get("infographic", "").strip(),
        "recipient": first_email(fields.get("recipient", "")),
        "vocalGender": gender,
        "styleWeight": None,
        "weirdnessConstraint": None,
    }


def sender_allowed(from_addr):
    raw = (CONFIG.get("allowed_senders") or "").strip()
    if not raw:
        return False
    addr = email.utils.parseaddr(from_addr or "")[1].strip().lower()
    if not addr or "@" not in addr:
        return False
    domain = addr.rsplit("@", 1)[1]
    for value in raw.split(","):
        pattern = value.strip().lower()
        if not pattern:
            continue
        if "@" in pattern and not pattern.startswith("@"):
            if addr == pattern:
                return True
        elif domain == pattern.lstrip("@"):
            return True
    return False


def imap_connect(cfg):
    user = (cfg.get("gmail_user") or "").strip()
    pw = (cfg.get("gmail_app_password") or "").replace(" ", "")
    if not user or not pw:
        raise RuntimeError("Gmail address and app password are both required.")
    failures = []
    for host in IMAP_HOSTS:
        M = None
        connected = False
        try:
            M = imaplib.IMAP4_SSL(host, 993, ssl_context=_ssl_ctx(), timeout=GMAIL_TIMEOUT)
            M.login(user, pw)
            connected = True
            return M
        except imaplib.IMAP4.error as e:
            detail = str(e)
            if "Application-specific password required" in detail:
                raise RuntimeError("Gmail needs an app password (your normal password won't work). "
                                   "Turn on 2-Step Verification, then create one at "
                                   "myaccount.google.com/apppasswords")
            if "Invalid credentials" in detail:
                raise RuntimeError("Gmail rejected those credentials. Check the address and "
                                   "re-paste the 16-character app password.")
            raise RuntimeError(f"Gmail login failed: {detail}")
        except (OSError, TimeoutError) as e:
            failures.append(f"{host}: {e}")
        finally:
            if M is not None and not connected:
                try:
                    M.logout()
                except Exception:
                    pass
    raise RuntimeError("Could not reach Gmail IMAP on port 993 after trying both Gmail hosts. "
                       "This is a network connection problem, not an app-password error. "
                       "Check that your network/VPN permits secure IMAP. Details: " + "; ".join(failures))


def imap_select_label(M, label):
    """Gmail exposes labels as IMAP folders. Try the label, then a few variants."""
    for name in (label, f"INBOX/{label}", label.replace(" ", "_")):
        try:
            typ, _ = M.select(f'"{name}"', readonly=True)
            if typ == "OK":
                return name
        except imaplib.IMAP4.error:
            continue
    raise RuntimeError(f'No Gmail label named "{label}". Create it in Gmail '
                       f'(Settings > Labels) and add a filter that applies it.')


def fetch_requests(cfg, seen, limit=60, first_run=False):
    """Returns (list_of_new_items, label_used). Never mutates the mailbox.

    Mail that arrived while the app was closed IS picked up: we scan the label
    and skip by remembered Message-ID rather than by unread flag."""
    M = imap_connect(cfg)
    try:
        label = imap_select_label(M, (cfg.get("gmail_label") or "SunoStudio").strip())
        typ, data = M.search(None, "ALL")
        if typ != "OK":
            return [], label
        ids = (data[0] or b"").split()[-limit:]
        cutoff = time.time() - FIRST_RUN_DAYS * 86400
        found = []
        for num in reversed(ids):
            # Download headers first. Re-fetching full bodies for every one of
            # the last 60 emails made ordinary polling slow enough to time out
            # on labels containing large HTML messages or attachments.
            typ, headers = M.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE SUBJECT FROM)])")
            if typ != "OK" or not headers or not headers[0]:
                continue
            header_msg = email.message_from_bytes(headers[0][1])
            mid = decode_header(header_msg.get("Message-ID")) or f"{label}:{num.decode()}"
            if mid in seen:
                continue
            # BODY.PEEK leaves the unread flag untouched.
            typ, raw = M.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            try:
                when = email.utils.parsedate_to_datetime(msg.get("Date"))
                when_str = when.strftime("%b %d, %H:%M")
                when_ts = when.timestamp()
            except Exception:
                when_str, when_ts = "", time.time()
            # First launch on a fresh install: don't dump the whole label into
            # the inbox. Anything older than the cutoff is quietly marked seen.
            if first_run and when_ts < cutoff:
                seen[mid] = 1
                continue
            subject = decode_header(msg.get("Subject"))
            from_addr = decode_header(msg.get("From"))
            body = body_text(msg)
            form = parse_request(subject, body, CONFIG.get("default_style", ""))
            if not form["lyrics"].strip() and not form["instrumental"] and not form["style"]:
                seen[mid] = 1      # nothing usable in it; don't keep re-reading
                continue
            print(f"[watch] parsed: title={form['title']!r} style={form['style'][:32]!r} "
                  f"sprint={form.get('tagline','')!r} to={form.get('recipient','')!r} "
                  f"lyric_chars={len(form['lyrics'])}")
            if form["title"] == "Untitled" or not form["style"]:
                print(f"[watch] body began: {body[:200]!r}")
            found.append({
                "id": uuid.uuid4().hex,
                "mid": mid,
                "from": from_addr,
                "subject": subject,
                "received": when_str,
                "form": form,
            })
        return found, label
    finally:
        try:
            M.logout()
        except Exception:
            pass


CHECK_NOW = threading.Event()
LAST_BALANCE_CHECK = [0.0]


def watch_loop():
    first_run = not SEEN_PATH.exists()
    seen = load_seen()
    # If a prior process stopped between persisting INBOX and SEEN, the durable
    # pending records are authoritative and must not be enqueued a second time.
    with INBOX_LOCK:
        for item in INBOX.values():
            if item.get("mid"):
                seen[item["mid"]] = 1
    save_seen(seen)
    catching_up = True          # the launch pass is the "what did I miss" pass
    while True:
        if not CONFIG.get("watch_enabled"):
            WATCH.update(state="off", message="watcher is off")
            CHECK_NOW.wait(3)
            CHECK_NOW.clear()
            continue
        try:
            if time.time() - LAST_BALANCE_CHECK[0] > 3600:
                LAST_BALANCE_CHECK[0] = time.time()
                try:
                    check_balances()
                except Exception as e:
                    print(f"[alert] balance check failed: {e}")
            WATCH.update(state="checking", message="checking Gmail...")
            new, label = fetch_requests(CONFIG, seen, first_run=first_run)
            first_run = False
            auto = 0
            for item in new:
                seen[item["mid"]] = 1
                if CONFIG.get("auto_generate") and sender_allowed(item["from"]):
                    start_job(item["form"], source=f"auto: {item['from'][:40]}")
                    auto += 1
                else:
                    with INBOX_LOCK:
                        INBOX[item["id"]] = item
                        _save_inbox_locked()
            save_seen(seen)
            if new:
                print(f"[watch] {len(new)} new request(s); {auto} auto-started")
            with INBOX_LOCK:
                waiting = len(INBOX)
            if catching_up and new:
                msg = (f"{len(new)} request(s) arrived while the app was closed"
                       + (f", {auto} started automatically" if auto else ""))
            else:
                msg = f'watching "{label}" - {waiting} waiting'
            catching_up = False
            WATCH.update(state="ok", last_check=time.time(), message=msg)
        except Exception as e:
            detail = str(e) or type(e).__name__
            WATCH.update(state="error", message=f"Gmail check failed: {detail}. Retrying in 30s.")
            print(f"[watch] {detail}")
            CHECK_NOW.wait(30)
            CHECK_NOW.clear()
        try:
            gap = max(15, int(CONFIG.get("watch_seconds") or 60))
        except (TypeError, ValueError):
            gap = 60
        # Interruptible: the Check now button fires this early.
        CHECK_NOW.wait(gap)
        CHECK_NOW.clear()


# --------------------------------------------------------------------------
# web server
# --------------------------------------------------------------------------

MAX_REQUEST_BYTES = 5 * 1024 * 1024


class BadRequest(Exception):
    pass


class RequestTooLarge(Exception):
    pass


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # keep the terminal clean

    # ---- helpers ----
    def _send(self, code, body: bytes, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest("invalid Content-Length")
        if n < 0:
            raise BadRequest("invalid Content-Length")
        if n > MAX_REQUEST_BYTES:
            raise RequestTooLarge
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            raise BadRequest("invalid JSON body")

    # ---- routes ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")

        if u.path == "/api/config":
            cls = PROVIDERS.get(CONFIG.get("provider"), KieAi)
            return self._json({
                "provider": CONFIG.get("provider"),
                "output_dir": CONFIG.get("output_dir"),
                "save_lyrics": CONFIG.get("save_lyrics"),
                "has_key": bool(CONFIG.get(f"{CONFIG.get('provider')}_key", "").strip()),
                "keys_set": {p: bool(CONFIG.get(f"{p}_key", "").strip()) for p in PROVIDERS},
                "models": cls.models,
                "exact_lyrics": cls.supports_exact_lyrics,
                "providers": [{"id": p, "label": c.label, "exact": c.supports_exact_lyrics}
                              for p, c in PROVIDERS.items()],
                "config_path": str(CONFIG_PATH),
                "version": APP_VERSION,
                "watch_enabled": CONFIG.get("watch_enabled"),
                "gmail_user": CONFIG.get("gmail_user"),
                "gmail_label": CONFIG.get("gmail_label"),
                "watch_seconds": CONFIG.get("watch_seconds"),
                "default_style": CONFIG.get("default_style"),
                "auto_generate": CONFIG.get("auto_generate"),
                "allowed_senders": CONFIG.get("allowed_senders"),
                "max_concurrent": CONFIG.get("max_concurrent"),
                "gmail_pw_set": bool((CONFIG.get("gmail_app_password") or "").strip()),
                "auto_video": CONFIG.get("auto_video"),
                "video_height": CONFIG.get("video_height"),
                "video_dir": CONFIG.get("video_dir"),
                "lyric_aligner": CONFIG.get("lyric_aligner", "section"),
                "hybrid_repair": CONFIG.get("hybrid_repair", "local"),
                "stable_ts": stable_ts_runtime_status(),
                "copy_path": CONFIG.get("copy_path"),
                "alerts_enabled": CONFIG.get("alerts_enabled"),
                "kie_low_credits": CONFIG.get("kie_low_credits"),
                "visualizer": CONFIG.get("visualizer"),
                "shimmer": CONFIG.get("shimmer", True),
                "interlude_mode": CONFIG.get("interlude_mode", True),
                "lyric_focus_band": CONFIG.get("lyric_focus_band", True),
                "bg_source": CONFIG.get("bg_source"),
                "art_title": CONFIG.get("art_title"),
                "openai_key_set": bool((CONFIG.get("openai_key") or "").strip()),
                "openai_image_model": CONFIG.get("openai_image_model"),
                "image_prompt_schema": CONFIG.get("image_prompt_schema", IMAGE_PROMPT_SCHEMA),
                "image_prompt_fragments": CONFIG.get("image_prompt_fragments", {}),
                "image_prompt_defaults": IMAGE_PROMPT_DEFAULTS,
                "staging_dir": CONFIG.get("staging_dir", ""),
                "rejects_dir": CONFIG.get("rejects_dir", ""),
                "reject_purge_days": CONFIG.get("reject_purge_days", 14),
                "gate_song": CONFIG.get("gate_song", False),
                "gate_image": CONFIG.get("gate_image", False),
                "gate_video": CONFIG.get("gate_video", False),
                "suno_single_clip": CONFIG.get("suno_single_clip", True),
                "ffmpeg": find_ffmpeg() or "",
                "ffmpeg_missing": missing_filters(find_ffmpeg()) if find_ffmpeg() else [],
            })

        if u.path == "/api/jobs":
            with JOBS_LOCK:
                jobs = json.loads(json.dumps(sorted(
                    JOBS.values(), key=lambda j: j["created"], reverse=True)))
            with INBOX_LOCK:
                inbox = json.loads(json.dumps(sorted(
                    INBOX.values(), key=lambda i: i["received"], reverse=True)))
            return self._json({
                "jobs": jobs,
                "inbox": inbox,
                "watch": WATCH,
                "alerts": ALERTS[-4:],
            })

        if u.path == "/api/prompt/preview":
            prompt, negatives = assemble_image_prompt(
                (q.get("title") or ["Test Song"])[0],
                (q.get("style") or [""])[0],
                (q.get("infographic") or [""])[0],
                (q.get("draw_title") or ["1"])[0] != "0",
                (q.get("tagline") or [""])[0])
            return self._json({"prompt": prompt, "negatives": negatives,
                               "warnings": validate_image_prompt_fragments(
                                   CONFIG.get("image_prompt_fragments"))})

        if u.path == "/file":
            path = (q.get("p") or [""])[0]
            return self._serve_file(path)

        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            body = self._body()
        except RequestTooLarge:
            self.close_connection = True
            return self._json({"error": "request body is too large"}, 413)
        except BadRequest as e:
            return self._json({"error": str(e)}, 400)

        if u.path == "/api/config":
            for k in ("provider", "output_dir", "video_dir", "staging_dir", "rejects_dir", "kie_key", "sunoapi_key", "atlascloud_key",
                      "openai_key", "openai_image_model", "todoist_token", "todoist_project",
                      "gmail_user", "gmail_app_password", "gmail_label",
                      "default_style", "allowed_senders"):
                if k in body and body[k] is not None:
                    v = body[k]
                    # blank secret submission = leave the stored one alone
                    if (k.endswith("_key") or k.endswith("password")) and not str(v).strip():
                        continue
                    CONFIG[k] = str(v).strip()
            if body.get("bg_source") in ("gradient", "ai"):
                CONFIG["bg_source"] = body["bg_source"]
            if "visualizer" in body and body["visualizer"] in ("bars","wave","off"):
                CONFIG["visualizer"] = body["visualizer"]
            if body.get("lyric_aligner") in ("section", "stable-ts-hybrid", "legacy"):
                CONFIG["lyric_aligner"] = body["lyric_aligner"]
            if body.get("hybrid_repair") in ("cloud", "local"):
                CONFIG["hybrid_repair"] = body["hybrid_repair"]
            for k in ("save_lyrics", "watch_enabled", "auto_generate", "auto_video",
                      "alerts_enabled",
                      "art_title", "copy_path", "shimmer", "suno_single_clip",
                      "gate_song", "gate_image", "gate_video",
                      "interlude_mode", "lyric_focus_band"):
                if k in body:
                    CONFIG[k] = bool(body[k])
            for k in ("watch_seconds", "max_concurrent", "video_height", "video_fps",
                      "video_crf", "kie_low_credits", "reject_purge_days"):
                if k in body:
                    try:
                        CONFIG[k] = max(1, int(body[k]))
                    except (TypeError, ValueError):
                        pass
            if "image_prompt_fragments" in body:
                raw = body.get("image_prompt_fragments") or {}
                if isinstance(raw, dict):
                    CONFIG["image_prompt_fragments"] = {
                        k: str(v) for k, v in raw.items() if k in IMAGE_PROMPT_DEFAULTS
                    }
                    CONFIG["image_prompt_schema"] = IMAGE_PROMPT_SCHEMA
            warnings = validate_image_prompt_fragments(CONFIG.get("image_prompt_fragments"))
            save_config(CONFIG)
            return self._json({"ok": True, "warnings": warnings})

        if u.path == "/api/art/test":
            key = (CONFIG.get("openai_key") or "").strip()
            if not key:
                return self._json({"ok": False, "message":
                                   "No OpenAI API key saved."})
            # Walk from the exact configured request down to the simplest one
            # that any model should accept, so a failure says WHICH bit broke.
            mdl = CONFIG.get("openai_image_model") or "gpt-image-2"
            title = body.get("title") or "Test Song"
            style = body.get("style") or "70s soul, horn section"
            bal = None
            probes = [(mdl, True, f"{mdl} with lettering"),
                      (mdl, False, f"{mdl} without lettering")]
            notes = []
            for mdl, letter, label in probes:
                try:
                    dest = Path(os.path.expanduser(CONFIG["output_dir"])) / "_art_test.jpg"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    generate_background_image(key, title, style, dest, model=mdl,
                                              draw_title=letter,
                                              tagline=body.get("tagline") or "",
                                              timeout=240)
                    kb = dest.stat().st_size / 1024
                    msg = f"Worked: {label} - {kb:.0f} KB."
                    if notes:
                        msg += "  Failed first: " + "; ".join(notes)
                    return self._json({"ok": True, "file": str(dest), "message": msg})
                except Exception as e:
                    notes.append(f"{label} -> {str(e)[:110]}")
                    print(f"[art] probe {label} failed: {e}")
            head = "Image generation failed."
            return self._json({"ok": False,
                               "message": head + "  " + "; ".join(notes)
                                          + "   (full detail in ~/Library/Logs/SunoStudio.log)"})

        if u.path == "/api/gmail/test":
            probe = dict(CONFIG)
            if (body.get("gmail_user") or "").strip():
                probe["gmail_user"] = body["gmail_user"].strip()
            if (body.get("gmail_app_password") or "").strip():
                probe["gmail_app_password"] = body["gmail_app_password"].strip()
            if (body.get("gmail_label") or "").strip():
                probe["gmail_label"] = body["gmail_label"].strip()
            try:
                M = imap_connect(probe)
                try:
                    label = imap_select_label(M, probe.get("gmail_label") or "SunoStudio")
                    typ, data = M.search(None, "ALL")
                    n = len((data[0] or b"").split()) if typ == "OK" else 0
                finally:
                    try:
                        M.logout()
                    except Exception:
                        pass
                return self._json({"ok": True,
                                   "message": f'Connected. Label "{label}" has {n} message(s).'})
            except Exception as e:
                return self._json({"ok": False, "message": str(e)})

        if u.path == "/api/inbox/approve":
            with INBOX_LOCK:
                item = INBOX.pop(body.get("id"), None)
                if item:
                    _save_inbox_locked()
            if not item:
                return self._json({"error": "that request is no longer pending"}, 404)
            form = dict(item["form"])
            if body.get("form"):                      # edited in the UI before approving
                form.update({k: v for k, v in body["form"].items() if v is not None})
            start_job(form, source=f"email: {item['from'][:40]}")
            return self._json({"ok": True})

        if u.path == "/api/watch/check":
            if not CONFIG.get("watch_enabled"):
                return self._json({"error": "the Gmail watcher is turned off"}, 400)
            CHECK_NOW.set()
            return self._json({"ok": True})

        if u.path == "/api/job/action":
            try:
                pipeline_action(body.get("job") or "", body.get("action") or "",
                                body.get("fields") if isinstance(body.get("fields"), dict) else None,
                                body.get("selected") if isinstance(body.get("selected"), dict) else None,
                                body.get("prompt") if isinstance(body.get("prompt"), str) else None)
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 400)

        if u.path == "/api/video":
            jid = body.get("job")
            try:
                idx = int(body.get("track") or 0)
            except (TypeError, ValueError):
                return self._json({"error": "invalid track index"}, 400)
            with JOBS_LOCK:
                job = JOBS.get(jid)
                track = (dict(job["tracks"][idx])
                         if job and 0 <= idx < len(job["tracks"]) else None)
            if not track:
                return self._json({"error": "can't find that track any more"}, 404)
            if not find_ffmpeg():
                return self._json({"error": "ffmpeg isn't installed. In Terminal:  "
                                            "brew install ffmpeg-full"}, 400)
            return self._json({"ok": True, "id": start_video_job(track)})

        if u.path == "/api/inbox/dismiss":
            with INBOX_LOCK:
                INBOX.pop(body.get("id"), None)
                _save_inbox_locked()
            return self._json({"ok": True})

        if u.path == "/api/inbox/approve_all":
            with INBOX_LOCK:
                items = list(INBOX.values())
                INBOX.clear()
                _save_inbox_locked()
            for item in items:
                start_job(item["form"], source=f"email: {item['from'][:40]}")
            return self._json({"ok": True, "count": len(items)})

        if u.path == "/api/generate":
            form = {
                "title": (body.get("title") or "").strip(),
                "style": (body.get("style") or "").strip(),
                "lyrics": body.get("lyrics") or "",
                "model": body.get("model") or "",
                "instrumental": bool(body.get("instrumental")),
                "negativeTags": (body.get("negativeTags") or "").strip(),
                "tagline": (body.get("tagline") or "").strip(),
                "recipient": first_email(body.get("recipient") or ""),
                "vocalGender": body.get("vocalGender") or "",
                "styleWeight": body.get("styleWeight"),
                "weirdnessConstraint": body.get("weirdnessConstraint"),
            }
            if not form["lyrics"].strip() and not form["style"] and not form["instrumental"]:
                return self._json({"error": "Give me some lyrics or at least a style."}, 400)
            return self._json({"ok": True, "id": start_job(form, source="manual")})

        if u.path == "/api/reveal":
            target = Path(os.path.expanduser(body.get("path") or str(final_video_root())))
            try:
                if platform.system() == "Darwin":
                    # `open file.mp4` launches the media player. Reveal the
                    # file instead, which selects its exact location in Finder.
                    subprocess.run(["open", "-R", str(target)] if target.is_file()
                                   else ["open", str(target)], check=False)
                elif platform.system() == "Windows":
                    os.startfile(str(target))  # noqa
                else:
                    subprocess.run(["xdg-open", str(target)], check=False)
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 500)

        if u.path == "/api/clear":
            with JOBS_LOCK:
                for k in [k for k, v in JOBS.items()
                          if v.get("status") in ("done", "completed", "error", "cancelled", "interrupted")]:
                    del JOBS[k]
                    JOB_FORMS.pop(k, None)
                _save_jobs_locked()
            return self._json({"ok": True})

        if u.path == "/api/quit":
            with JOBS_LOCK:
                busy = [j for j in JOBS.values()
                        if j["status"] in ("queued", "submitting", "running")]
            if busy and not body.get("force"):
                return self._json({"busy": len(busy)})
            self._json({"ok": True})
            print("shutting down (asked to quit from the UI)")
            threading.Thread(target=lambda: (time.sleep(0.4), os._exit(0)), daemon=True).start()
            return

        return self._send(404, b"not found", "text/plain")

    def _find_moved(self, p):
        """The Drive notifier moves finished mp4s into _sent, which breaks the
        stored path. Look for the same filename nearby before giving up."""
        roots = []
        for d in (CONFIG.get("video_dir"), CONFIG.get("output_dir")):
            if (d or "").strip():
                roots.append(Path(os.path.expanduser(d)))
        roots.append(p.parent.parent)
        for r in roots:
            try:
                if not r.is_dir():
                    continue
                for cand in r.rglob(p.name):
                    if cand.is_file():
                        return cand
            except Exception:
                continue
        return None

    def _serve_file(self, path):
        """Serve an audio/image file, but only from inside the output dir."""
        try:
            root = Path(os.path.expanduser(CONFIG["output_dir"])).resolve()
            p = Path(os.path.expanduser(path)).resolve()
            if not p.is_file():
                moved = self._find_moved(p)
                if moved:
                    print(f"[serve] {p.name} moved -> {moved}")
                    p = moved.resolve()
                else:
                    raise FileNotFoundError
            ok = False
            # Newly generated MP3s remain in the private staging root until a
            # pipeline gate approves them. They are still legitimate media for
            # this UI, so authorize the exact configured staging tree too.
            for d in (CONFIG.get("output_dir"), CONFIG.get("video_dir"),
                      CONFIG.get("staging_dir"), str(pipeline_root("staging"))):
                if (d or "").strip():
                    try:
                        p.relative_to(Path(os.path.expanduser(d)).resolve())
                        ok = True
                        break
                    except ValueError:
                        pass
            if not ok:
                raise PermissionError
        except Exception:
            return self._send(403, b"forbidden", "text/plain")

        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        size = p.stat().st_size
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                if "," in rng:
                    raise ValueError("multiple ranges are not supported")
                s, _, e = rng[6:].partition("-")
                if not s:
                    length = int(e)
                    if length <= 0:
                        raise ValueError
                    start = max(0, size - length)
                    end = size - 1
                else:
                    start = int(s)
                    end = int(e) if e else size - 1
                end = min(end, size - 1)
                if start < 0 or start >= size or end < start:
                    raise ValueError
                return self._stream_file(p, ctype, start, end, size, partial=True)
            except Exception:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        return self._stream_file(p, ctype, 0, size - 1, size, partial=False)

    def _stream_file(self, path, ctype, start, end, size, partial=False):
        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>Suno Studio</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#0d0e12; --panel:#15171d; --panel2:#1b1e26; --line:#282c38;
  --ink:#e8eaf0; --dim:#8b91a3; --accent:#7c5cff; --accent2:#22d3a6;
  --warn:#ffb020; --err:#ff5d6c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif}
a{color:var(--accent2)}
header{display:flex;align-items:center;gap:14px;padding:16px 24px;
  border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:10}
h1{font-size:17px;margin:0;font-weight:650;letter-spacing:-.01em}
.logo{width:28px;height:28px;border-radius:8px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));flex:none}
.spacer{flex:1}
.wrap{display:grid;grid-template-columns:minmax(380px,1fr) minmax(380px,1fr);
  gap:20px;padding:20px 24px;max-width:1500px;margin:0 auto;align-items:start}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
label{display:block;font-size:12px;font-weight:600;color:var(--dim);
  text-transform:uppercase;letter-spacing:.06em;margin:14px 0 6px}
label:first-child{margin-top:0}
input[type=text],textarea,select{width:100%;background:var(--panel2);color:var(--ink);
  border:1px solid var(--line);border-radius:9px;padding:10px 12px;font:inherit;outline:none}
input:focus,textarea:focus,select:focus{border-color:var(--accent)}
textarea{resize:vertical;min-height:260px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:13.5px;line-height:1.65;white-space:pre}
.row{display:flex;gap:12px}.row>*{flex:1}
.hint{font-size:12px;color:var(--dim);margin-top:6px}
button{background:var(--accent);color:#fff;border:0;border-radius:9px;
  padding:11px 18px;font:inherit;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.12)}
button:disabled{opacity:.45;cursor:default;filter:none}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--dim);font-weight:500}
button.ghost:hover{color:var(--ink);border-color:var(--dim);filter:none}
.small{padding:6px 11px;font-size:12.5px;border-radius:7px}
.check{display:flex;align-items:center;gap:9px;font-size:14px;color:var(--ink);
  text-transform:none;letter-spacing:0;font-weight:500;margin:0;cursor:pointer}
.check input{width:16px;height:16px;accent-color:var(--accent);margin:0}
details{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
summary{cursor:pointer;font-size:12px;font-weight:600;color:var(--dim);
  text-transform:uppercase;letter-spacing:.06em;list-style:none}
summary::-webkit-details-marker{display:none}
summary:before{content:"› ";display:inline-block;transition:.15s}
details[open] summary:before{transform:rotate(90deg)}
.actions{display:flex;gap:10px;align-items:center;margin-top:20px;
  border-top:1px solid var(--line);padding-top:16px}
.btns{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.banner{background:rgba(255,176,32,.09);border:1px solid rgba(255,176,32,.3);
  color:var(--warn);border-radius:9px;padding:10px 12px;font-size:13px;margin-bottom:14px}
.job{background:var(--panel2);border:1px solid var(--line);border-radius:11px;
  padding:0;margin:0 0 11px}
.job h3{margin:0 0 3px;font-size:15px;font-weight:600}
.job .meta{font-size:12.5px;color:var(--dim)}
.job>summary{padding:13px 15px;border:0;text-transform:none;letter-spacing:0;position:relative}
.job>summary:before{position:absolute;right:15px;top:15px;font-size:18px}
.job>summary h3{padding-right:24px;color:var(--ink)}
.job>.job-body{padding:0 15px 13px}
.job:not([open])>summary .meta{margin-top:3px}
.progress{height:7px;margin-top:9px;background:var(--line);border-radius:999px;overflow:hidden}
.progress>span{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));
  border-radius:inherit;transition:width .35s ease}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;
  text-transform:uppercase;padding:3px 8px;border-radius:20px;margin-left:8px;vertical-align:2px}
.b-run{background:rgba(124,92,255,.16);color:#a48fff}
.b-done{background:rgba(34,211,166,.14);color:var(--accent2)}
.b-err{background:rgba(255,93,108,.14);color:var(--err)}
.b-new{background:rgba(34,211,166,.14);color:var(--accent2);margin-left:6px}
.ib{background:var(--panel2);border:1px solid var(--line);border-left:3px solid var(--accent2);
  border-radius:11px;padding:13px 15px;margin-bottom:11px}
.ib h3{margin:0 0 2px;font-size:15px;font-weight:600}
.ib .src{font-size:12px;color:var(--dim);margin-bottom:9px;word-break:break-all}
.ib pre{margin:0 0 11px;padding:9px 11px;background:var(--bg);border-radius:7px;
  font-family:ui-monospace,Menlo,monospace;font-size:12px;line-height:1.55;color:var(--dim);
  max-height:150px;overflow:auto;white-space:pre-wrap}
.ib .btns{display:flex;gap:8px}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:1px}
.d-ok{background:var(--accent2)}.d-off{background:var(--line)}.d-err{background:var(--err)}
.watchbar{font-size:12px;color:var(--dim);padding:9px 12px;background:var(--panel2);
  border:1px solid var(--line);border-radius:9px;margin-bottom:14px}
.settings-section{border-top:1px solid var(--line);margin-top:20px;padding-top:14px}
.settings-section>h4{margin:0 0 3px;font-size:14px;color:var(--ink)}
.gate-panel{margin:12px 0;padding:12px;border:1px solid var(--accent);background:rgba(124,92,255,.08);border-radius:10px}
.gate-panel .check{color:var(--ink);text-transform:none;letter-spacing:0;font-size:14px;margin:10px 0 2px}
.gate-panel .hint{margin:0 0 10px 25px}
.image-selected-preview{margin-top:10px;position:relative}
.image-selected-preview img{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;
  background:var(--bg);border:2px solid var(--accent2);border-radius:9px;cursor:zoom-in}
.image-preview-label{position:absolute;left:9px;bottom:9px;padding:3px 7px;border-radius:6px;
  background:rgba(8,10,15,.82);color:#fff;font-size:11px;font-weight:600}
.image-variants{display:flex;gap:9px;overflow-x:auto;margin-top:10px;padding:2px 1px 8px;
  scroll-snap-type:x proximity}
.image-thumb{flex:0 0 150px;padding:0;border:2px solid var(--line);border-radius:8px;
  overflow:hidden;background:var(--bg);scroll-snap-align:start}
.image-thumb:hover{filter:none;border-color:var(--dim)}
.image-thumb:disabled{opacity:1;cursor:default}
.image-thumb img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover}
.image-thumb.selected{border-color:var(--accent2);box-shadow:0 0 0 1px var(--accent2)}
.image-viewer{width:min(94vw,1400px);max-width:none;padding:12px}
.image-viewer img{display:block;max-width:100%;max-height:82vh;margin:auto;border-radius:8px}
.image-viewer-bar{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.trk{margin-top:11px;padding-top:11px;border-top:1px solid var(--line)}
.trk .fn{font-size:12.5px;color:var(--dim);margin-bottom:6px;
  word-break:break-all;font-family:ui-monospace,Menlo,monospace}
audio{width:100%;height:34px;filter:invert(.92) hue-rotate(180deg)}
.empty{color:var(--dim);font-size:14px;text-align:center;padding:40px 10px}
.spin{display:inline-block;width:11px;height:11px;border:2px solid var(--line);
  border-top-color:var(--accent);border-radius:50%;animation:sp .7s linear infinite;
  margin-right:7px;vertical-align:-1px}
@keyframes sp{to{transform:rotate(360deg)}}
dialog{background:var(--panel);color:var(--ink);border:1px solid var(--line);
  border-radius:14px;padding:22px;max-width:520px;width:92%}
dialog::backdrop{background:rgba(0,0,0,.6)}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.vtag{font-size:11px;font-weight:600;color:var(--dim);background:var(--panel2);
  border:1px solid var(--line);border-radius:20px;padding:2px 8px;margin-left:8px;
  vertical-align:2px;letter-spacing:.03em}
</style></head><body>

<header>
  <div class="logo"></div>
  <h1>Suno Studio <span id="ver" class="vtag"></span></h1>
  <div class="spacer"></div>
  <button class="ghost small" onclick="reveal('')">Open delivery folder</button>
  <button class="ghost small" onclick="openSettings()">Settings</button>
  <button class="ghost small" onclick="quitApp()">Quit</button>
</header>

<div class="wrap">
  <div>
    <div id="warn"></div>
    <div class="banner" id="alertbar" style="display:none"></div>
    <div class="watchbar" id="watchbar" style="display:none"></div>

    <div class="card" id="inboxcard" style="display:none;margin-bottom:20px">
      <div style="display:flex;align-items:center;margin-bottom:12px">
        <label style="margin:0">From Rovo <span id="ibcount" class="badge b-new"></span></label>
        <div class="spacer"></div>
        <button class="ghost small" onclick="approveAll()">Generate all</button>
      </div>
      <div id="inbox"></div>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;margin-bottom:12px">
        <label style="margin:0">Active Jobs</label>
      </div>
      <div id="activejobs"><div class="empty">No active jobs.</div></div>
    </div>
  </div>

  <div>
    <div class="card">
      <div style="display:flex;align-items:center;margin-bottom:12px">
        <label style="margin:0">Finished Jobs</label>
        <div class="spacer"></div>
        <button class="ghost small" onclick="clearDone()">Clear Finished</button>
      </div>
      <div id="finishedjobs"><div class="empty">No finished jobs.</div></div>
    </div>
  </div>
</div>

<dialog id="dlg">
  <h3 style="margin:0 0 4px">Settings</h3>
  <div class="hint">Changes are saved locally when you choose Save.</div>
  <div class="gate-panel">
    <b>Approval gates</b>
    <div class="hint">Turn a gate on to stop the pipeline at that stage until you approve it. Turn all three off for hands-off processing.</div>
    <label class="check"><input type="checkbox" id="s_gate_song"> Require approval after song generation</label>
    <div class="hint">Choose or edit a generated song before artwork starts.</div>
    <label class="check"><input type="checkbox" id="s_gate_image"> Require approval after image generation</label>
    <div class="hint">Choose or regenerate artwork before lyric-video rendering starts.</div>
    <label class="check"><input type="checkbox" id="s_gate_video"> Require approval after video rendering</label>
    <div class="hint">Inspect the finished video before publishing it to Final.</div>
  </div>
  <div class="settings-section"><h4>Song creation &amp; folders</h4>
  <label>Provider</label>
  <select id="s_provider" onchange="providerChanged()"></select>
  <div class="hint" id="s_provhint"></div>

  <label>API key</label>
  <input type="password" id="s_key" placeholder="paste key (leave blank to keep current)">
  <div class="hint" id="s_keystate"></div>

  <label>Output folder</label>
  <input type="text" id="s_out">
  <div class="hint">Each song gets its own dated subfolder.</div>

  <label class="check" style="margin-top:14px"><input type="checkbox" id="s_lyr"> Save a .txt with lyrics + settings</label>

  <details>
    <summary>Pipeline storage &amp; capacity</summary>
    <div class="row"><div><label>Staging folder</label><input id="s_stage" placeholder="default: beside Final"></div><div><label>Rejects folder</label><input id="s_rejects" placeholder="default: beside Final"></div></div>
    <div class="row"><div><label>External calls at once</label><input id="s_cap" placeholder="2"></div><div><label>Reject purge (days)</label><input id="s_purge" placeholder="14"></div></div>
    <label class="check"><input type="checkbox" id="s_oneclip"> Prefer one Suno clip per request</label>
    <div class="hint">Current Suno API generation returns two clips and offers no supported count parameter; both are retained as variants.</div>
  </details>
  </div>

  <div class="settings-section"><h4>Gmail intake</h4>
  <label class="check" style="margin-top:14px"><input type="checkbox" id="s_watch"> Watch Gmail for song requests</label>
  <div class="hint">Reads one label over IMAP. Never marks, moves, or deletes mail.</div>

  <div class="row">
    <div><label>Gmail address</label><input type="text" id="s_gu" placeholder="you@gmail.com"></div>
    <div><label>Label</label><input type="text" id="s_gl" placeholder="SunoStudio"></div>
  </div>
  <label>App password</label>
  <input type="password" id="s_gp" placeholder="16 characters (leave blank to keep current)">
  <div class="hint" id="s_gpstate"></div>

  <label>Default style <span style="text-transform:none;font-weight:400">(when an email doesn't specify one)</span></label>
  <input type="text" id="s_ds" placeholder="indie folk, acoustic guitar, warm">

  <div class="row">
    <div><label>Check every (sec)</label><input type="text" id="s_ws" placeholder="60"></div>
    <div><label>Max at once</label><input type="text" id="s_mc" placeholder="2"></div>
  </div>

  <label class="check" style="margin-top:14px"><input type="checkbox" id="s_auto"> Skip approval and generate automatically</label>
  <div class="hint">Only fires for senders listed below. Leave the list empty and nothing auto-fires.</div>
  <label>Trusted senders</label>
  <input type="text" id="s_as" placeholder="rovo@yourcompany.com, you@example.com">

  <div class="actions">
    <button class="ghost" onclick="testGmail()">Test Gmail connection</button>
    <span class="hint" style="margin:0" id="s_test"></span>
  </div>
  </div>

  <div class="settings-section"><h4>Alerts</h4>
  <label class="check" style="margin-top:14px"><input type="checkbox" id="s_al"> Warn me before the accounts run dry</label>
  <div class="hint">Checks kie.ai hourly. OpenAI publishes no balance, so that one is
  caught the first time it reports an exhausted quota.</div>
  <div class="row">
    <div><label>Warn under (kie.ai credits)</label><input type="text" id="s_alk" placeholder="100"></div>
    <div><label>Todoist API token</label><input type="password" id="s_tdt" placeholder="optional"></div>
  </div>
  </div>

  <div class="settings-section"><h4>Lyric video</h4>
  <div class="row">
    <div><label style="margin-top:0">Resolution</label>
      <select id="s_vh"><option value="1080">1080p</option><option value="720">720p (faster)</option></select></div>
    <div><label style="margin-top:0">Background</label>
      <select id="s_bg"><option value="gradient">Generated gradient</option><option value="ai">AI artwork</option></select></div>
    <div><label style="margin-top:0">Visualizer</label>
      <select id="s_vis"><option value="bars">Spectrum bars</option><option value="wave">Waveform</option><option value="off">None</option></select></div>
  </div>
  <label>Lyric timing</label>
  <select id="s_align">
    <option value="section">Suno timing + section-safe alignment</option>
    <option value="stable-ts-hybrid">Local stable-ts hybrid (recommended)</option>
    <option value="legacy">Legacy whole-song alignment</option>
  </select>
  <div class="hint" id="s_alignstate"></div>
  <label>Weak-line repair</label>
  <select id="s_repair">
    <option value="cloud">Hosted whisper-1 (best quality)</option>
    <option value="local">Fully local forced alignment</option>
  </select>
  <div class="hint">Hosted repair sends only short audio windows that stable-ts flags,
  using the saved OpenAI key. Accepted results are cached beside the song.</div>
  <label>Video output folder <span style="text-transform:none;font-weight:400">(blank = beside the mp3)</span></label>
  <input type="text" id="s_vd" placeholder="~/Dropbox/SongVideos  - a watch folder, say">
  <div class="hint">Only the finished .mp4 goes here. Audio, artwork and subtitles stay with the song.</div>
  </div>

  <div class="settings-section"><h4>Image artwork</h4>
  <div class="hint" style="margin-top:8px">AI artwork is <b>off by default</b> - switch Background to
  "AI artwork" above. It uses your OpenAI key; the optional lyric focus band adds one masked image edit.</div>
  <label class="check" style="margin-top:14px"><input type="checkbox" id="s_arttitle"> Let the AI artwork letter the title (skips the drawn title)</label>
  <label class="check" style="margin-top:9px"><input type="checkbox" id="s_shimmer"> Add animated sparkle shimmer to bright parts of the artwork</label>
  <label class="check" style="margin-top:9px"><input type="checkbox" id="s_interlude"> Interlude mode: stronger gold glints during lyric-free breaks</label>
  <label class="check" style="margin-top:9px"><input type="checkbox" id="s_focus"> Add a feathered lyric focus band while words are being sung</label>
  <div class="hint">The focus band is 90% opaque and fades opposite interlude mode. With AI artwork it uses one additional masked image edit; if that edit fails, a local FFmpeg version is used automatically.</div>
  <label>Image model</label>
  <select id="s_im">
    <option value="gpt-image-2">gpt-image-2 (best)</option>
    <option value="gpt-image-1">gpt-image-1 (cheaper)</option>
  </select>
  <details open>
    <summary>Default image prompt (editable)</summary>
    <div class="hint">Edit these prompt building blocks to change every new image without a rebuild—seasonal themes included. Edited text stays yours; Reset restores the shipped wording.</div>
    <div id="s_fragments"></div>
    <button class="ghost small" onclick="previewPrompt()">Preview using current song fields</button>
    <textarea id="s_promptpreview" readonly style="min-height:120px"></textarea>
  </details>
  <label>OpenAI API key</label>
  <input type="password" id="s_ok" placeholder="sk-... (leave blank to keep current)">
  <div class="hint" id="s_okstate"></div>
  <div class="actions" style="border:0;padding-top:8px;margin-top:8px">
    <button class="ghost" onclick="testArt(this)">Test image generation</button>
    <span class="hint" style="margin:0" id="s_arttest"></span>
  </div>
  <label class="check" style="margin-top:12px"><input type="checkbox" id="s_av"> Render a lyric video automatically after each song</label>
  <div class="hint" id="s_ffstate"></div>
  </div>

  <div class="actions">
    <button onclick="saveSettings()">Save</button>
    <button class="ghost" onclick="dlg.close()">Cancel</button>
    <div class="spacer"></div>
  </div>
  <div class="hint mono" id="s_path"></div>
</dialog>

<dialog id="imageviewer" class="image-viewer" onclick="if(event.target===this)this.close()">
  <div class="image-viewer-bar"><b id="imageviewer_label">Selected Image</b><div class="spacer"></div>
    <button class="ghost small" onclick="$('imageviewer').close()">Close</button></div>
  <img id="imageviewer_img" alt="Full-size selected image">
</dialog>

<script>
let CFG = null;
const $ = id => document.getElementById(id);

async function loadConfig(){
  CFG = await (await fetch('/api/config')).json();
  const p = CFG.providers.find(x=>x.id===CFG.provider) || {};
  $('ver').textContent = 'v' + (CFG.version || '?');
  document.title = 'Suno Studio v' + (CFG.version || '?');
  let w = '';
  if(!CFG.has_key) w = 'No API key set for '+(p.label||'this provider')+'. Open Settings to add one.';
  else if(!CFG.exact_lyrics) w = p.label+" can't sing your lyrics verbatim — it only takes a free-text prompt, so Suno will paraphrase. Switch to sunoapi.org for exact lyrics.";
  $('warn').innerHTML = w ? `<div class="banner">${w}</div>` : '';
}

async function generate(){
  const body = {
    title: $('title').value, style: $('style').value, lyrics: $('lyrics').value,
    infographic: $('infographic').value,
    tagline: $('sprint').value, recipient: $('recip').value,
    model: $('model').value, instrumental: $('instrumental').checked,
    negativeTags: $('neg').value, vocalGender: $('gender').value,
    styleWeight: $('sw').value || null, weirdnessConstraint: $('wc').value || null,
  };
  $('go').disabled = true;
  const r = await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j = await r.json();
  $('go').disabled = false;
  if(j.error){ alert(j.error); return; }
  refresh();
}

function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

let INBOX = [];

function renderInbox(inbox, watch){
  INBOX = inbox || [];
  const card = $('inboxcard');
  const bar = $('watchbar');
  if(watch && CFG && CFG.watch_enabled){
    const cls = watch.state==='error'?'d-err':watch.state==='off'?'d-off':'d-ok';
    bar.style.display='flex';
    bar.innerHTML = `<span><span class="dot ${cls}"></span>${esc(watch.message||'')}</span>`
      + `<span style="flex:1"></span>`
      + `<button class="ghost small" onclick="checkNow(this)">Check now</button>`;
  } else { bar.style.display='none'; }

  const al=(watch&&watch.alerts)||window.__alerts||[];
  if(!INBOX.length){ card.style.display='none'; return; }
  card.style.display='block';
  $('ibcount').textContent = INBOX.length;
  $('inbox').innerHTML = INBOX.map(it=>{
    const f = it.form || {};
    const preview = (f.instrumental ? '(instrumental)' : (f.lyrics||'')).split('\n').slice(0,8).join('\n');
    const bits = [f.style||'no style', f.model||'default model'];
    if(f.tagline) bits.unshift(f.tagline);
    if(f.recipient) bits.push('-> '+f.recipient);
    if(f.instrumental) bits.push('instrumental');
    return `<div class="ib">
      <h3>${esc(f.title||'Untitled')}</h3>
      <div class="src">${esc(it.from||'')}${it.received?' · '+esc(it.received):''} · ${esc(bits.join(' · '))}</div>
      <pre>${esc(preview)}</pre>
      <div class="btns">
        <button class="small" onclick="approve('${it.id}')">Generate Song</button>
        <button class="ghost small" onclick="dismiss('${it.id}')">Dismiss</button>
      </div></div>`;
  }).join('');
}

async function approve(id){
  await fetch('/api/inbox/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  refresh();
}
async function approveAll(){
  if(INBOX.length>1 && !confirm(`Generate all ${INBOX.length} requests? Each one costs credits.`)) return;
  await fetch('/api/inbox/approve_all',{method:'POST'}); refresh();
}
async function dismiss(id){
  await fetch('/api/inbox/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  refresh();
}
function edit(id){
  const it = INBOX.find(x=>x.id===id); if(!it) return;
  const f = it.form || {};
  $('title').value = f.title||''; $('style').value = f.style||'';
  $('infographic').value = f.infographic||'';
  $('sprint').value = f.tagline||'';
  $('recip').value = f.recipient||'';
  $('lyrics').value = f.lyrics||''; $('instrumental').checked = !!f.instrumental;
  $('neg').value = f.negativeTags||''; $('gender').value = f.vocalGender||'';
  if(f.model && [...$('model').options].some(o=>o.value===f.model)) $('model').value = f.model;
  dismiss(id);
  window.scrollTo({top:0,behavior:'smooth'});
  $('title').focus();
}

// Repainting the queue would tear down any <audio> that's mid-playback, so
// only touch the DOM when the markup actually changed AND nothing is playing.
let lastActiveHtml = '', lastFinishedHtml = '', pendingJobsHtml = null;
const COLLAPSED_JOBS = new Set();

function rememberJobState(id, el){
  if(el.open) COLLAPSED_JOBS.delete(id); else COLLAPSED_JOBS.add(id);
}

function mediaBusy(el){
  return [...el.querySelectorAll('audio,video')].some(m => !m.paused && !m.ended && m.currentTime > 0);
}

function paintJobs(activeHtml, finishedHtml){
  const active = $('activejobs'), finished = $('finishedjobs');
  if(mediaBusy(active) || mediaBusy(finished)){
    pendingJobsHtml = [activeHtml, finishedHtml]; return;
  }
  if(activeHtml !== lastActiveHtml){ active.innerHTML = activeHtml; lastActiveHtml = activeHtml; }
  if(finishedHtml !== lastFinishedHtml){ finished.innerHTML = finishedHtml; lastFinishedHtml = finishedHtml; }
  pendingJobsHtml = null;
}

async function pipelineAction(job, action, prompt, fields, selected){
  const r = await (await fetch('/api/job/action',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,action,prompt,fields,selected})})).json();
  if(r.error) alert(r.error); refresh();
}
async function cancelAndRemove(job){
  if(!confirm('Cancel this job and remove it from Suno Studio? Generated files will be moved to the recoverable Rejects folder.')) return;
  await pipelineAction(job,'cancel_remove');
}
function fieldId(job,key){ return 'pf_'+job+'_'+key; }
function gateFields(job){
  const value = key => $(fieldId(job,key))?.value;
  return {title:value('title'),tagline:value('tagline'),style:value('style'),
    lyrics:value('lyrics'),infographic:value('infographic')};
}
function deliveryFields(job){
  const fields=gateFields(job), recipient=$(fieldId(job,'recipient'))?.value;
  if(recipient !== undefined) fields.recipient=recipient;
  return fields;
}
function saveGateFields(job){ pipelineAction(job,'save_fields',null,gateFields(job)); }
function selectVariant(job,stage,id){ pipelineAction(job,'save_fields',null,null,{stage,id}); }
function songPicker(j){
  const locked=j.stage==='video'&&j.video_song_id, selected=locked||j.selected_song;
  const variants=(j.song_variants||[]).map((v,i)=>`<option value="${v.id}" ${v.id===selected?'selected':''}>Song ${i+1} · ${new Date((v.created||0)*1000).toLocaleTimeString()}</option>`).join('');
  if(!variants) return '';
  return `<label>Selected Song</label><select id="ps_${j.id}" ${locked?'disabled':''} onchange="selectVariant('${j.id}','song',this.value)">${variants}</select>${locked?'<div class="hint">Locked to the song used by this video.</div>':''}`;
}
function songEditor(j){
  const f=j.current_fields||{}; const id=j.id;
  return `<div class="btns" style="margin-top:10px"><button class="ghost small" onclick="toggleSongEditor('${id}',this)">Edit Song Details</button></div><div id="pe_${id}" style="display:none"><div class="row"><div><label>Title</label><input id="${fieldId(id,'title')}" value="${esc(f.title||'')}"></div><div><label>Tagline</label><input id="${fieldId(id,'tagline')}" value="${esc(f.tagline||'')}"></div></div><label>Genre / Style</label><input id="${fieldId(id,'style')}" value="${esc(f.style||'')}"><label>Lyrics</label><textarea id="${fieldId(id,'lyrics')}" style="min-height:120px">${esc(f.lyrics||'')}</textarea><label>Infographic</label><textarea id="${fieldId(id,'infographic')}" style="min-height:80px">${esc(f.infographic||'')}</textarea><div class="btns"><button class="ghost small" onclick="saveGateFields('${id}')">Save Song Details</button><button class="ghost small" onclick="pipelineAction('${id}','revert_email')">Revert To Email Original</button></div>${j.stale_song?'<div class="hint" style="color:var(--warn)">Lyrics or genre changed: generate a new song before continuing.</div>':''}</div>`;
}
function toggleSongEditor(id, btn){
  const editor=$('pe_'+id), opening=editor.style.display==='none';
  editor.style.display=opening?'block':'none';
  btn.textContent=opening?'Hide Song Details':'Edit Song Details';
}
function openImagePreview(src, label){
  $('imageviewer_img').src=src;
  $('imageviewer_label').textContent=label||'Selected Image';
  $('imageviewer').showModal();
}
function imagePicker(j){
  if(!(j.image_variants||[]).length) return '';
  const locked=j.stage==='video'&&j.video_image_id, selected=locked||j.selected_image;
  const opts=(j.image_variants||[]).map((v,i)=>`<option value="${v.id}" ${v.id===selected?'selected':''}>Image ${i+1} · ${new Date((v.created||0)*1000).toLocaleTimeString()}</option>`).join('');
  const current=(j.image_variants||[]).find(v=>v.id===selected)||{};
  const currentIndex=Math.max(0,(j.image_variants||[]).findIndex(v=>v.id===selected));
  const currentUrl=current.file?`/file?p=${encodeURIComponent(current.file)}`:'';
  const gallery=(j.image_variants||[]).map((v,i)=>v.file?`<button type="button" class="image-thumb ${v.id===selected?'selected':''}" ${locked?'disabled':''} aria-label="Select Image ${i+1}" onclick="selectVariant('${j.id}','image','${v.id}')"><img alt="Image ${i+1}" src="/file?p=${encodeURIComponent(v.file)}"></button>`:'').join('');
  const preview=currentUrl?`<div class="image-selected-preview"><img alt="Selected Image ${currentIndex+1}" src="${currentUrl}" onclick="openImagePreview(this.src,this.alt)"><span class="image-preview-label">Image ${currentIndex+1} of ${(j.image_variants||[]).length} · Click To Enlarge</span></div>`:'';
  return `<details open><summary>Image Generation · ${j.image_regenerations||0} Additional Images</summary><label>Selected Image</label><select id="pi_${j.id}" ${locked?'disabled':''} onchange="selectVariant('${j.id}','image',this.value)">${opts}</select>${locked?'<div class="hint">Locked to the gallery image used by this video.</div>':''}${preview}<div class="image-variants" aria-label="Image Options">${gallery}</div><label>Image Prompt</label><textarea id="pp_${j.id}" style="min-height:150px">${esc(current.prompt||'')}</textarea></details>`;
}
function pipelineButtons(j, songTracks, videoTracks, progress){
  if(!j.pipeline) return '';
  const id = j.id;
  const songs=`<details open><summary>Song Generation</summary>${songTracks}${songPicker(j)}${songEditor(j)}</details>`;
  const images=imagePicker(j);
  const video=(j.stage==='video'||videoTracks) ? `<details open><summary>Video Generation</summary>${progress}<div class="hint" style="margin-top:8px">${esc(j.message||'')}</div>${videoTracks}</details>` : '';
  const fields=`gateFields('${id}')`;
  const newSong=`<button class="ghost small" onclick="pipelineAction('${id}','resubmit_song',null,${fields})">Generate New Song</button>`;
  const anotherImage=`<button class="ghost small" onclick="pipelineAction('${id}','regenerate_image',document.getElementById('pp_${id}')?.value,${fields})">Generate Another Image</button>`;
  const cancel=`<button class="ghost small" onclick="cancelAndRemove('${id}')">Cancel And Remove</button>`;
  const running=(j.status==='queued'||j.status==='running'||j.status==='submitting');
  const stop=running?`<div class="btns" style="margin-top:10px"><button class="ghost small" onclick="pipelineAction('${id}','interrupt')">Interrupt</button>${cancel}</div>`:'';
  if(j.status==='paused_song') return songs+`<div class="btns" style="margin-top:10px"><button class="small" ${j.stale_song?'disabled':''} onclick="pipelineAction('${id}','approve_song',null,${fields})">Generate Image</button>${newSong}${cancel}</div>`;
  if(j.status==='paused_image') return songs+images+`<div class="btns" style="margin-top:10px"><button class="small" onclick="pipelineAction('${id}','approve_image',null,${fields},{stage:'image',id:document.getElementById('pi_${id}').value})">Generate Video</button>${anotherImage}${newSong}${cancel}</div>`;
  if(j.status==='error' && j.stage==='song' && j.task_id) return songs+`<div class="btns" style="margin-top:10px"><button class="small" onclick="pipelineAction('${id}','retry_song_poll')">Retry Provider Status</button>${newSong}${cancel}</div>`;
  if(j.status==='error' && j.stage==='image') return songs+images+`<div class="btns" style="margin-top:10px">${anotherImage}${newSong}${cancel}</div>`;
  if(j.status==='paused_video') {
    const recipient=esc((j.current_fields||{}).recipient||'');
    return songs+images+video+`<div class="delivery-review"><label>Delivery Email <span class="hint">(review or change before publishing)</span></label><input type="email" id="${fieldId(id,'recipient')}" value="${recipient}" placeholder="name@example.com"><div class="hint">The approved video is routed using this email after you publish it.</div></div><div class="btns" style="margin-top:10px"><button class="small" onclick="pipelineAction('${id}','approve_video',null,deliveryFields('${id}'))">Publish Final</button>${anotherImage}${newSong}${cancel}</div>`;
  }
  if(running && j.stage==='image') return songs+images+`<div class="hint" style="margin-top:10px"><span class="spin"></span>Generating Another Image — Existing Options Remain Available.</div>`+stop;
  if(running && j.stage==='video') return songs+images+video+stop;
  if(j.status==='error') return songs+images+video+`<div class="btns" style="margin-top:10px">${cancel}</div>`;
  return songs+images+video+stop;
}

function trackMarkup(j, t, ti){
  if(t.video) return `<div class="trk"><div class="fn">${esc(t.name)}</div>
    <video controls preload="metadata" style="width:100%;border-radius:7px;background:#000"
      src="/file?p=${encodeURIComponent(t.file)}"></video></div>`;
  const wc = t.words ? `<span class="hint" style="margin-left:8px">${t.words} words timed</span>` : '';
  return `<div class="trk"><div class="fn">${esc(t.name)}${t.duration?' · '+Math.round(t.duration)+'s':''}</div>
    <audio controls preload="none" src="/file?p=${encodeURIComponent(t.file)}"></audio><div>${wc}</div></div>`;
}

function collapsedMessage(j){
  if(j.status==='paused_song') return 'Waiting for Song Approval';
  if(j.status==='paused_image') return 'Waiting for Image Approval';
  if(j.status==='paused_video') return 'Waiting for Video Approval';
  return j.message||'';
}

async function refresh(){
  const {jobs, inbox, watch, alerts} = await (await fetch('/api/jobs')).json();
  const ab=$('alertbar');
  if(alerts && alerts.length){ ab.style.display='block';
    ab.innerHTML = alerts.map(a=>`<div><b>${esc(a.headline)}</b> ${esc(a.detail||'')}</div>`).join(''); }
  else ab.style.display='none';
  renderInbox(inbox, watch);
  if(pendingJobsHtml !== null && !mediaBusy($('activejobs')) && !mediaBusy($('finishedjobs'))){
    paintJobs(pendingJobsHtml[0], pendingJobsHtml[1]);
  }
  if(!jobs.length){ paintJobs('<div class="empty">No active jobs.</div>', '<div class="empty">No finished jobs.</div>'); return; }
  const renderGroup = group => group.map(j=>{
    const run = j.status==='queued'||j.status==='submitting'||j.status==='running';
    const paused = ['paused_song','paused_image','paused_video'].includes(j.status);
    const badge = run ? '<span class="badge b-run">working</span>'
      : paused ? '<span class="badge b-new">approval needed</span>'
      : (j.status==='done'||j.status==='completed') ? '<span class="badge b-done">done</span>'
      : j.status==='cancelled' ? '<span class="badge b-err">cancelled</span>'
      : '<span class="badge b-err">failed</span>';
    const songSource = j.pipeline ? (j.song_variants||[]).map(v=>v.track).filter(Boolean)
                                  : (j.tracks||[]).filter(t=>!t.video);
    const seenSongs = new Set();
    const songTracks = songSource.filter(t=>t.file&&!seenSongs.has(t.file)&&seenSongs.add(t.file))
      .map((t,ti)=>trackMarkup(j,t,ti)).join('');
    const videoTracks=(j.tracks||[]).filter(t=>t.video).map((t,ti)=>trackMarkup(j,t,ti)).join('');
    const revealPath = j.final_path || j.folder;
    const btn = revealPath ? `<button class="ghost small" style="margin-top:11px"
        onclick="reveal(${JSON.stringify(revealPath).replace(/"/g,'&quot;')})">Reveal in Finder</button>` : '';
    const src = (j.source && j.source!=='manual') ? ' · '+esc(j.source) : '';
    const hasProgress = run && j.stage==='video' && j.phase==='render' &&
      j.progress!==null && j.progress!==undefined && Number.isFinite(Number(j.progress));
    const percent = hasProgress ? Math.max(0,Math.min(100,Number(j.progress))) : 0;
    const progress = hasProgress ? `<div class="progress" role="progressbar" aria-label="Video render progress"
      aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span style="width:${percent}%"></span></div>` : '';
    const body = j.pipeline
      ? pipelineButtons(j,songTracks,videoTracks,progress)
      : progress+songTracks+videoTracks;
    const open = COLLAPSED_JOBS.has(j.id) ? '' : ' open';
    return `<details class="job"${open} ontoggle="rememberJobState('${j.id}',this)">
      <summary><h3>${esc(j.title)}${badge}</h3>
      <div class="meta">${run?'<span class="spin"></span>':''}${esc(collapsedMessage(j))}</div></summary>
      <div class="job-body">
      <div class="meta">${esc(j.created_str)}${src}</div>
      ${j.note?`<div class="meta" style="color:var(--warn);margin-top:3px">${esc(j.note)}</div>`:''}
      ${body}${btn}</div></details>`;
  }).join('');
  const finishedStatuses = new Set(['done','completed','cancelled','interrupted']);
  const active = jobs.filter(j => !finishedStatuses.has(j.status));
  const finished = jobs.filter(j => finishedStatuses.has(j.status));
  paintJobs(active.length ? renderGroup(active) : '<div class="empty">No active jobs.</div>',
            finished.length ? renderGroup(finished) : '<div class="empty">No finished jobs.</div>');
}

async function checkNow(btn){
  btn.disabled = true; btn.textContent = 'checking...';
  const r = await (await fetch('/api/watch/check',{method:'POST'})).json();
  if(r.error) alert(r.error);
  setTimeout(refresh, 1200);
}

async function reveal(path){
  await fetch('/api/reveal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
}
async function clearDone(){ await fetch('/api/clear',{method:'POST'}); refresh(); }

async function quitApp(force){
  const r = await (await fetch('/api/quit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({force:!!force})})).json();
  if(r.busy){
    if(confirm(`${r.busy} song(s) still generating. Quit anyway and lose them?`)) return quitApp(true);
    return;
  }
  document.body.innerHTML = '<div style="text-align:center;padding:90px 20px;color:#8b91a3;'
    + 'font:15px -apple-system,sans-serif">Suno Studio has stopped.<br><br>'
    + '<span style="font-size:13px">You can close this tab. Reopen the app to start it again.</span></div>';
}

const dlg = $('dlg');
function openSettings(){
  $('s_provider').innerHTML = CFG.providers.map(p=>`<option value="${p.id}">${p.label}</option>`).join('');
  $('s_provider').value = CFG.provider;
  $('s_out').value = CFG.output_dir;
  $('s_lyr').checked = CFG.save_lyrics;
  $('s_key').value = '';
  $('s_watch').checked = !!CFG.watch_enabled;
  $('s_gu').value = CFG.gmail_user||'';
  $('s_gl').value = CFG.gmail_label||'SunoStudio';
  $('s_gp').value = '';
  $('s_gpstate').textContent = CFG.gmail_pw_set ? 'An app password is already saved.'
    : 'Needs 2-Step Verification, then myaccount.google.com/apppasswords';
  $('s_ds').value = CFG.default_style||'';
  $('s_ws').value = CFG.watch_seconds||60;
  $('s_mc').value = CFG.max_concurrent||2;
  $('s_cap').value = CFG.max_concurrent||2;
  $('s_stage').value = CFG.staging_dir||''; $('s_rejects').value = CFG.rejects_dir||'';
  $('s_purge').value = CFG.reject_purge_days||14;
  $('s_gate_song').checked=!!CFG.gate_song; $('s_gate_image').checked=!!CFG.gate_image;
  $('s_gate_video').checked=!!CFG.gate_video; $('s_oneclip').checked=CFG.suno_single_clip!==false;
  $('s_auto').checked = !!CFG.auto_generate;
  $('s_as').value = CFG.allowed_senders||'';
  $('s_test').textContent = '';
  $('s_vh').value = String(CFG.video_height||1080);
  $('s_vis').value = CFG.visualizer||'bars';
  $('s_align').value = CFG.lyric_aligner||'section';
  $('s_repair').value = CFG.hybrid_repair||'local';
  $('s_alignstate').textContent = (CFG.stable_ts&&CFG.stable_ts.message)||'';
  $('s_alignstate').style.color = (CFG.stable_ts&&CFG.stable_ts.ready) ? 'var(--accent2)' : 'var(--dim)';
  $('s_bg').value = CFG.bg_source||'gradient';
  $('s_vd').value = CFG.video_dir||'';
  $('s_al').checked = !!CFG.alerts_enabled;
  $('s_alk').value = CFG.kie_low_credits ?? 100;
  $('s_tdt').value = '';
  $('s_arttitle').checked = !!CFG.art_title;
  $('s_shimmer').checked = CFG.shimmer !== false;
  $('s_interlude').checked = CFG.interlude_mode !== false;
  $('s_focus').checked = CFG.lyric_focus_band !== false;
  $('s_ok').value = '';
  $('s_okstate').textContent = CFG.openai_key_set
    ? 'An OpenAI key is saved.'
    : 'Get one at platform.openai.com/api-keys (needs a paid balance).';
  if(CFG.openai_image_model) $('s_im').value = CFG.openai_image_model;
  renderFragments();
  $('s_av').checked = !!CFG.auto_video;
  const miss = CFG.ffmpeg_missing || [];
  $('s_ffstate').textContent = !CFG.ffmpeg ? 'ffmpeg not installed - run  brew install ffmpeg-full'
    : miss.length ? ('ffmpeg found, but missing: '+miss.join(', ')+' - run  brew install ffmpeg-full  (the plain ffmpeg formula lacks libass/freetype)')
    : ('ffmpeg found at '+CFG.ffmpeg);
  $('s_ffstate').style.color = (!CFG.ffmpeg || miss.length) ? 'var(--warn)' : 'var(--dim)';
  $('s_path').textContent = 'Suno Studio v' + (CFG.version||'?') + '  ·  settings stored in ' + CFG.config_path;
  providerChanged();
  dlg.showModal();
}

function renderFragments(){
  const d=CFG.image_prompt_defaults||{}, o=CFG.image_prompt_fragments||{};
  $('s_fragments').innerHTML=Object.keys(d).map(k=>`<label>${esc(k)}</label><textarea id="frag_${k}" style="min-height:88px">${esc(o[k]||d[k])}</textarea><button class="ghost small" onclick="resetFragment('${k}')">Reset to default</button>`).join('');
}
function resetFragment(k){ $('frag_'+k).value=(CFG.image_prompt_defaults||{})[k]||''; }
function readFragments(){ const d=CFG.image_prompt_defaults||{}, out={}; Object.keys(d).forEach(k=>{const v=$('frag_'+k).value; if(v!==d[k]) out[k]=v;}); return out; }
async function previewPrompt(){
  const q=new URLSearchParams({title:'Test Song',style:'',tagline:'',infographic:''});
  const r=await (await fetch('/api/prompt/preview?'+q)).json(); $('s_promptpreview').value=r.prompt||'';
}

async function testArt(btn){
  btn.disabled = true;
  $('s_arttest').textContent = 'generating (up to 2 min)...';
  $('s_arttest').style.color = 'var(--dim)';
  const r = await (await fetch('/api/art/test',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({title:'Test Song', style:'70s soul, horn section', tagline:''})})).json();
  $('s_arttest').textContent = r.message || '';
  $('s_arttest').style.color = r.ok ? 'var(--accent2)' : 'var(--err)';
  btn.disabled = false;
  if(r.ok && r.file) reveal(r.file);
}

async function testGmail(){
  $('s_test').textContent = 'connecting...';
  const r = await (await fetch('/api/gmail/test',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({gmail_user:$('s_gu').value, gmail_app_password:$('s_gp').value, gmail_label:$('s_gl').value})})).json();
  $('s_test').textContent = r.message || '';
  $('s_test').style.color = r.ok ? 'var(--accent2)' : 'var(--err)';
}
function providerChanged(){
  const id = $('s_provider').value;
  const p = CFG.providers.find(x=>x.id===id) || {};
  $('s_provhint').textContent = p.exact
    ? 'Custom Mode: your lyrics are sung exactly as written.'
    : 'Prompt-only: lyrics get folded into the prompt and will be paraphrased.';
  $('s_keystate').textContent = CFG.keys_set[id] ? 'A key is already saved for this provider.' : 'No key saved yet.';
}
async function saveSettings(){
  const id = $('s_provider').value;
  const body = {provider:id, output_dir:$('s_out').value,
                save_lyrics:$('s_lyr').checked,
                watch_enabled:$('s_watch').checked, gmail_user:$('s_gu').value,
                gmail_label:$('s_gl').value, default_style:$('s_ds').value,
                watch_seconds:$('s_ws').value, max_concurrent:$('s_cap').value,
                staging_dir:$('s_stage').value, rejects_dir:$('s_rejects').value,
                reject_purge_days:$('s_purge').value, gate_song:$('s_gate_song').checked,
                gate_image:$('s_gate_image').checked, gate_video:$('s_gate_video').checked,
                suno_single_clip:$('s_oneclip').checked, image_prompt_fragments:readFragments(),
                auto_generate:$('s_auto').checked, allowed_senders:$('s_as').value,
                video_height:$('s_vh').value, visualizer:$('s_vis').value, video_dir:$('s_vd').value, alerts_enabled:$('s_al').checked,
                lyric_aligner:$('s_align').value,
                hybrid_repair:$('s_repair').value,
                kie_low_credits:$('s_alk').value, bg_source:$('s_bg').value, art_title:$('s_arttitle').checked,
                shimmer:$('s_shimmer').checked,
                interlude_mode:$('s_interlude').checked,
                lyric_focus_band:$('s_focus').checked,
                openai_image_model:$('s_im').value,
                auto_video:$('s_av').checked};
  if($('s_gp').value.trim()) body.gmail_app_password = $('s_gp').value.trim();
  if($('s_ok').value.trim()) body.openai_key = $('s_ok').value.trim();
  if($('s_tdt').value.trim()) body.todoist_token = $('s_tdt').value.trim();
  if($('s_key').value.trim()) body[id+'_key'] = $('s_key').value.trim();
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  dlg.close(); await loadConfig();
}

loadConfig().then(refresh);
setInterval(refresh, 3000);
</script></body></html>
"""


# --------------------------------------------------------------------------

def main():
    Path(os.path.expanduser(CONFIG["output_dir"])).mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(CONFIG)

    url = f"http://{HOST}:{PORT}"
    print("=" * 62)
    print(f"  Suno Studio v{APP_VERSION}")
    print(f"  UI       {url}")
    print(f"  Output   {CONFIG['output_dir']}")
    print(f"  Config   {CONFIG_PATH}")
    key_set = bool(CONFIG.get(f"{CONFIG.get('provider')}_key", "").strip())
    print(f"  Provider {CONFIG.get('provider')}  " + ("(key set)" if key_set else "(NO KEY - add one in Settings)"))
    if CONFIG.get("watch_enabled"):
        mode = "auto-generate" if CONFIG.get("auto_generate") else "approval queue"
        print(f"  Gmail    watching \"{CONFIG.get('gmail_label')}\" ({mode})")
    else:
        print("  Gmail    watcher off")
    print("  Stop with the Quit button in the UI (or Ctrl-C if run from a terminal)")
    print("=" * 62)

    try:
        srv = Server((HOST, PORT), Handler)
    except OSError:
        # Almost always means Suno Studio is already running (double double-click).
        # Just surface the existing window instead of dying silently.
        already = False
        try:
            with urllib.request.urlopen(f"{url}/api/config", timeout=3) as r:
                already = r.status == 200
        except Exception:
            pass
        if already:
            print("Suno Studio is already running - opening the existing window.")
            webbrowser.open(url)
            sys.exit(0)
        print(f"\nCould not bind port {PORT}: something else is using it.")
        sys.exit(1)

    # Start background work only after this process proves it owns the port.
    # A second app launch must not duplicate polling, email ingestion, or jobs.
    schedule_reject_purge()
    threading.Thread(target=watch_loop, daemon=True).start()
    resume_persisted_jobs()
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        srv.shutdown()


if __name__ == "__main__":
    main()
