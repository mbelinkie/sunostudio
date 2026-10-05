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
import email.parser
from email.message import EmailMessage
import configparser
import csv
import colorsys
import hashlib
import http.server
import importlib
import imaplib
import json
import math
import mimetypes
import os
import platform
import re
import signal
import smtplib
import socketserver
import ssl
import subprocess
import shutil
import sys
import venv
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

APP_VERSION = "6.0.6"

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
    "slack_bot_token": "",              # optional bot token for local file delivery
    "gmail_label": "SunoStudio",
    "watch_seconds": 60,
    "default_style": "",
    "max_concurrent": 2,          # external API / encoder slots, not paused jobs
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
    "render_backend": "local",         # "local" | "aws"; opt in after AWS setup
    "aws_render_size": "large",         # measured fastest task; can change without rebuilding
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
    for stale in ("save_cover", "use_cover_art", "suno_single_clip"):
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


def atomic_write_text(path, value, mode=None):
    """Write text beside its destination, then replace it atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(value)
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
    refresh_aws_resources(cfg)
    atomic_write_json(CONFIG_PATH, cfg, mode=0o600)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass


AWS_RESOURCE_KEYS = (
    "aws_region", "aws_bucket", "aws_cluster", "aws_render_task",
    "aws_delivery_task", "aws_subnets", "aws_security_group", "aws_link_url",
    "aws_link_secret", "aws_account_id", "aws_profile",
)


def refresh_aws_resources(cfg):
    """Use task IDs written by AWS setup, even if this app was already running."""
    try:
        saved = json.loads(CONFIG_PATH.read_text())
        if isinstance(saved, dict):
            cfg.update({key: saved[key] for key in AWS_RESOURCE_KEYS if key in saved})
    except (OSError, ValueError):
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


class SlackDeliveryError(RuntimeError):
    """A Slack error with an explicit signal for possibly completed uploads."""
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = bool(uncertain)


def aws_settings():
    """Read resource IDs saved by setup_aws.py; importing AWS stays optional."""
    refresh_aws_resources(CONFIG)
    return {
        "profile": CONFIG.get("aws_profile"),
        "region": CONFIG.get("aws_region"), "bucket": CONFIG.get("aws_bucket"),
        "cluster": CONFIG.get("aws_cluster"),
        "render_task": CONFIG.get("aws_render_task"),
        "delivery_task": CONFIG.get("aws_delivery_task"),
        "subnets": CONFIG.get("aws_subnets"),
        "security_group": CONFIG.get("aws_security_group"),
        "link_url": CONFIG.get("aws_link_url"),
        "link_secret": CONFIG.get("aws_link_secret"),
        "render_size": CONFIG.get("aws_render_size", "large"),
    }


AWS_SUPPORT = CONFIG_DIR / f"aws-env-py{sys.version_info.major}{sys.version_info.minor}"
AWS_SETUP = {"status": "idle", "lines": [], "checked": None}
AWS_SETUP_LOCK = threading.Lock()


def aws_support_python():
    return AWS_SUPPORT / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def load_aws_support():
    """Make the app's private AWS packages available after setup and on restart."""
    packages = (AWS_SUPPORT / "Lib/site-packages" if os.name == "nt" else
                AWS_SUPPORT / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" /
                "site-packages")
    if packages.is_dir() and str(packages) not in sys.path:
        sys.path.append(str(packages))
        importlib.invalidate_caches()


def install_aws_support():
    requirements = Path(__file__).with_name("requirements-cloud.txt")
    if not requirements.is_file():
        raise RuntimeError("AWS support files are missing from this app download")
    stamp = AWS_SUPPORT / ".requirements-sha256"
    expected = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not aws_support_python().exists():
        venv.create(AWS_SUPPORT, with_pip=True)
    if not stamp.exists() or stamp.read_text().strip() != expected:
        result = subprocess.run([str(aws_support_python()), "-m", "pip", "install",
                                 "--disable-pip-version-check", "-r", str(requirements)],
                                capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError("Could not install AWS support. " +
                               (result.stderr or result.stdout).strip()[-400:])
        stamp.write_text(expected)
    load_aws_support()


load_aws_support()


def aws_profiles():
    config = configparser.RawConfigParser()
    config.read(Path.home() / ".aws" / "config")
    return sorted((section[8:] if section.startswith("profile ") else section)
                  for section in config.sections()
                  if section == "default" or section.startswith("profile "))


def aws_setup_snapshot():
    with AWS_SETUP_LOCK:
        checked = AWS_SETUP["checked"] or ("", "", "")
        return {"status": AWS_SETUP["status"], "lines": list(AWS_SETUP["lines"]),
                "checked_profile": checked[0], "checked_region": checked[1],
                "account": checked[2],
                "profiles": aws_profiles(), "ready": aws_ready()}


def run_aws_setup(action, profile, region, account=""):
    def report(line):
        # Never return credentials to a browser or persist setup output.
        line = re.sub(r"xox[baprs]-\S+|AKIA[A-Z0-9]{16}", "[redacted]", line.strip())
        if line:
            with AWS_SETUP_LOCK:
                AWS_SETUP["lines"] = (AWS_SETUP["lines"] + [line[:500]])[-30:]

    try:
        if action == "signin":
            aws = shutil.which("aws") or next((p for p in
                ("/usr/local/bin/aws", "/opt/homebrew/bin/aws") if Path(p).is_file()), None)
            if not aws:
                raise RuntimeError("Install AWS CLI v2 to sign in, then try again")
            cmd = [aws, "sso", "login"] + (["--profile", profile] if profile else [])
        else:
            report("Installing AWS support (first use may take a few minutes)…")
            install_aws_support()
            cmd = [str(aws_support_python()), "-u",
                   str(Path(__file__).with_name("setup_aws.py")), "--region", region]
            if profile:
                cmd += ["--profile", profile]
            if action == "check":
                cmd.append("--check")
            else:
                cmd += ["--account", account]
                token = (CONFIG.get("slack_bot_token") or "").strip()
                cmd.append("--slack-token-stdin" if token else "--no-slack")
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE if action == "setup" else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        if action == "setup":
            process.stdin.write((token or "") + "\n")
            process.stdin.close()
        for line in process.stdout:
            report(line)
        if process.wait():
            raise RuntimeError("AWS " + action + " failed. See the last message above.")
        with AWS_SETUP_LOCK:
            if action == "check":
                output = "\n".join(AWS_SETUP["lines"])
                match = re.search(r"AWS account (\d{12}); region ([a-z0-9-]+);", output)
                if not match or match.group(2) != region:
                    raise RuntimeError("AWS identity check returned no account")
                AWS_SETUP["checked"] = (profile, region, match.group(1))
                AWS_SETUP["status"] = "checked"
            else:
                if action == "setup":
                    saved = json.loads(CONFIG_PATH.read_text())
                    CONFIG.update({k: v for k, v in saved.items() if k.startswith("aws_")})
                AWS_SETUP["status"] = "done"
    except Exception as error:
        report(str(error))
        with AWS_SETUP_LOCK:
            AWS_SETUP["status"] = "error"


def start_aws_setup(action, profile, region):
    if action not in ("signin", "check", "setup"):
        raise ValueError("Choose Sign in, Check account, or Set up AWS")
    if not re.fullmatch(r"[A-Za-z0-9_.@/-]{0,80}", profile or ""):
        raise ValueError("AWS profile name contains unsupported characters")
    if not re.fullmatch(r"[a-z0-9-]{3,32}", region or ""):
        raise ValueError("Enter a valid AWS region")
    with AWS_SETUP_LOCK:
        if AWS_SETUP["status"] in ("signing_in", "checking", "setting_up"):
            raise ValueError("An AWS setup step is already running")
        checked = AWS_SETUP["checked"]
        if action == "setup" and (not checked or checked[:2] != (profile, region)):
            raise ValueError("Check this AWS account and region before creating resources")
        AWS_SETUP.update(status={"signin": "signing_in", "check": "checking",
                                 "setup": "setting_up"}[action], lines=[])
        if action != "setup":
            AWS_SETUP["checked"] = None
    threading.Thread(target=run_aws_setup,
                     args=(action, profile, region, checked[2] if checked else ""),
                     daemon=True).start()


def aws_ready():
    try:
        import aws_render
        import boto3  # noqa: F401
        aws_render._config(aws_settings())
        return True
    except (ImportError, ValueError):
        return False


def aws_signin_status():
    """Check the selected backend without exposing AWS identity to the browser."""
    if CONFIG.get("render_backend") != "aws":
        return "local"
    if not aws_ready():
        return "setup"
    try:
        import boto3
        from botocore.config import Config as BotoConfig
        settings = aws_settings()
        session = boto3.Session(profile_name=settings.get("profile") or None,
                                region_name=settings["region"])
        sts = session.client("sts", config=BotoConfig(
            connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}))
        account = sts.get_caller_identity().get("Account")
        return "ready" if account == CONFIG.get("aws_account_id") else "wrong_account"
    except Exception:
        return "signin"


def email_link_for_job(job):
    import aws_render
    return aws_render.email_link_for_file(aws_settings(), job["id"], job["final_path"])


def cloud_slack_for_job(job):
    """Dispatch once; an uncertain completion must be reviewed before retry."""
    import aws_render
    config = aws_settings()
    render_attempt = job.get("cloud_revision_execution") or job.get("cloud_execution") or {}
    render_result = render_attempt.get("result") or {}
    cloud_video = (render_result.get("output_uri")
                   if render_attempt.get("status") == "succeeded" else None)
    execution = job.get("cloud_delivery")
    if not execution:
        attempt = uuid.uuid4().hex
        set_job(job["id"], cloud_delivery={"attempt_id": attempt, "status": "dispatching",
                "result_uri": f"s3://{config['bucket']}/delivery-results/{job['id']}/{attempt}/result.json"})
        try:
            execution = aws_render.dispatch_slack_delivery(
                config, job["id"], attempt, cloud_video or job["final_path"],
                job["delivery_destination"],
                (job.get("current_fields") or {}).get("caption") or "",
                Path(job["final_path"]).name)
        except Exception as error:
            execution = aws_render.reconcile_render(config, {
                "attempt_id": attempt,
                "result_uri": f"s3://{config['bucket']}/delivery-results/{job['id']}/{attempt}/result.json"})
            if not execution.get("task_arn") and not execution.get("result"):
                raise SlackDeliveryError(
                    "AWS Slack dispatch is unconfirmed; inspect the saved attempt before retrying.",
                    uncertain=True) from error
        set_job(job["id"], cloud_delivery=execution)
    else:
        execution = aws_render.reconcile_render(config, execution)
        set_job(job["id"], cloud_delivery=execution)
        if not execution.get("task_arn") and not execution.get("result"):
            raise SlackDeliveryError("Cloud delivery dispatch is unconfirmed; review AWS before retrying.", uncertain=True)
    if execution.get("status") == "succeeded":
        result = execution.get("result") or {}
        return {"provider": "slack", "file_id": result.get("file_id"),
                "channel_id": result.get("channel_id"),
                "permalink": (result.get("slack_file") or {}).get("permalink", "")}
    while True:
        state = aws_render.wait_or_poll_render(config, execution, wait_seconds=15)
        set_job(job["id"], cloud_delivery=state)
        if state["status"] == "running":
            continue
        result = state.get("result") or {}
        if state["status"] == "succeeded":
            return {"provider": "slack", "file_id": result.get("file_id"),
                    "channel_id": result.get("channel_id"),
                    "permalink": (result.get("slack_file") or {}).get("permalink", "")}
        raise SlackDeliveryError(result.get("error") or "Cloud Slack delivery failed",
                                 uncertain=state["status"] in ("uncertain", "failed"))


EMAIL_LINK_FACTORY = email_link_for_job
CLOUD_DELIVERY_DISPATCHER = cloud_slack_for_job

_DELIVERY_LOCKS = {}
_DELIVERY_LOCKS_GUARD = threading.Lock()
DELIVERY_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
SLACK_CHANNEL_RE = re.compile(r"^C[A-Z0-9]+$")


def valid_delivery_email(value):
    """Return one validated mailbox from an address or a display-name form."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        addresses = email.utils.getaddresses([raw])
    except (TypeError, ValueError):
        return ""
    if len(addresses) != 1:
        return ""
    address = (addresses[0][1] or "").strip()
    if not DELIVERY_EMAIL_RE.fullmatch(address):
        return ""
    return address.lower()


def delivery_details(fields):
    """Resolve explicit delivery choice and return (mode, destination, error)."""
    fields = fields if isinstance(fields, dict) else {}
    raw_mode = fields.get("delivery_mode")
    mode = str(raw_mode if raw_mode is not None else "none").strip().lower()
    if not mode or mode in {"no", "no delivery", "off"}:
        mode = "none"
    if mode not in {"none", "slack", "email"}:
        return mode, "", "Choose Slack, Email, or None for delivery."
    if mode == "none":
        return mode, "", ""
    if mode == "email":
        raw = fields.get("recipient") or fields.get("delivery_destination") or ""
        destination = valid_delivery_email(raw)
        if not destination:
            return mode, "", "Enter a valid recipient email address, or choose None."
        return mode, destination, ""
    raw = str(fields.get("slack_channel_id") or fields.get("delivery_destination") or "").strip()
    if not SLACK_CHANNEL_RE.fullmatch(raw):
        return mode, "", "Enter a Slack channel ID beginning with C, or choose None."
    return mode, raw, ""


def normalize_delivery_fields(fields):
    """Copy a request form while preserving its explicit delivery selection."""
    result = dict(fields or {})
    caption = re.sub(r"\s+", " ", str(result.get("caption") or "")).strip()
    if len(caption) >= 240:
        raise ValueError("Song Caption must be shorter than 240 characters.")
    result["caption"] = caption
    mode, destination, error = delivery_details(result)
    result["delivery_mode"] = mode
    raw_recipient = str(result.get("recipient") or "").strip()
    result["recipient"] = valid_delivery_email(raw_recipient) or raw_recipient
    result["slack_channel_id"] = str(result.get("slack_channel_id") or "").strip()
    result["delivery_destination"] = destination
    if error:
        result["delivery_error"] = error
    else:
        result.pop("delivery_error", None)
    return result


def _delivery_lock(job_id):
    with _DELIVERY_LOCKS_GUARD:
        return _DELIVERY_LOCKS.setdefault(job_id, threading.Lock())


def slack_api_post(method, token, payload, uncertain_on_transport=False):
    upload_slot = method == "files.getUploadURLExternal"
    request = urllib.request.Request(
        "https://slack.com/api/" + method,
        data=(urllib.parse.urlencode(payload).encode("utf-8") if upload_slot else
              json.dumps(payload).encode("utf-8")), method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": ("application/x-www-form-urlencoded" if upload_slot else
                                  "application/json; charset=utf-8")})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise SlackDeliveryError(f"Slack {method} request failed: {error}",
                                 uncertain=uncertain_on_transport) from error
    if result.get("ok") is not True:
        raise SlackDeliveryError(
            f"Slack {method} failed: {result.get('error', 'unknown response')}",
            uncertain=uncertain_on_transport and "ok" not in result)
    return result


def slack_upload_mp4(video_path, channel_id, token, caption=""):
    """Upload one MP4 with Slack's external-file upload flow."""
    video_path = Path(video_path)
    if not video_path.is_file():
        raise RuntimeError("the finished MP4 is missing")
    if not token:
        raise RuntimeError("Add a Slack bot token in Settings before sending to Slack.")
    size = video_path.stat().st_size
    filename = video_path.name
    slot = slack_api_post("files.getUploadURLExternal", token,
                          {"filename": filename, "length": size})
    file_id, upload_url = slot.get("file_id"), slot.get("upload_url")
    if not file_id or not upload_url:
        raise SlackDeliveryError("Slack did not return an upload URL and file ID.")

    boundary = "suno-studio-" + uuid.uuid4().hex
    safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    data = video_path.read_bytes()
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body += data + f"\r\n--{boundary}--\r\n".encode("ascii")
    request = urllib.request.Request(
        upload_url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Slack upload returned HTTP {response.status}")
    except Exception as error:
        # The upload URL alone does not share a file to the channel; completion
        # is a separate API call, so retrying cannot duplicate a channel post.
        code = getattr(error, "code", None)
        detail = f"HTTP {code}" if code else "network or server error"
        raise SlackDeliveryError(f"Slack file upload failed ({detail}).") from error

    complete = slack_api_post(
        "files.completeUploadExternal", token,
        {"files": [{"id": file_id, "title": filename}],
         "channel_id": channel_id,
         "initial_comment": caption or f"Video: {filename}"},
        uncertain_on_transport=True)
    file_info = (complete.get("files") or [{}])[0]
    return {"provider": "slack", "file_id": file_id,
            "channel_id": channel_id, "title": filename,
            "permalink": file_info.get("permalink") or ""}


def send_email_delivery(job):
    fields = job.get("current_fields") or {}
    recipient = job.get("delivery_destination") or delivery_details(fields)[1]
    user = (CONFIG.get("gmail_user") or "").strip()
    password = (CONFIG.get("gmail_app_password") or "").strip()
    if not user or not password:
        raise RuntimeError("Add your Gmail address and app password in Settings before sending email.")
    if not callable(EMAIL_LINK_FACTORY):
        raise RuntimeError("Private email links need AWS setup. The local MP4 is still saved.")
    signed = EMAIL_LINK_FACTORY(job)
    url = signed.get("url") if isinstance(signed, dict) else signed
    if not isinstance(url, str) or not url.startswith("https://"):
        raise RuntimeError("The private download link could not be created.")
    message = EmailMessage()
    message["Subject"] = f"Your video: {job.get('title') or 'Suno Studio'}"
    message["From"] = user
    message["To"] = recipient
    message["Message-ID"] = email.utils.make_msgid()
    caption = (fields.get("caption") or "").strip()
    message.set_content(
        (f"{caption}\n\n" if caption else "Your approved Suno Studio video is ready.\n\n") +
        f"Download it privately here: {url}\n\n"
        "This link expires in three days. Your local MP4 remains available in Suno Studio.")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                          context=ssl.create_default_context(), timeout=45) as smtp:
        smtp.login(user, password)
        refused = smtp.send_message(message)
    if refused:
        raise RuntimeError("Gmail refused delivery to: " + ", ".join(sorted(refused)))
    return {"provider": "gmail-smtp", "message_id": message["Message-ID"],
            "recipient": recipient,
            "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}


def send_job_delivery(job_id, allow_uncertain_retry=False):
    """Send only an approved, published MP4 and store outcome apart from render state."""
    lock = _delivery_lock(job_id)
    if not lock.acquire(blocking=False):
        return
    try:
        job = job_snapshot(job_id)
        if not job or job.get("status") != "completed" or not job.get("final_path"):
            set_job(job_id, delivery_status="error",
                    delivery_error="The approved local MP4 is not available for delivery.")
            return
        if job.get("delivery_status") == "sent":
            return
        if job.get("delivery_status") == "needs_review" and not allow_uncertain_retry:
            return
        fields = job.get("current_fields") or {}
        mode, destination, error = delivery_details(fields)
        if error:
            set_job(job_id, delivery_mode=mode, delivery_destination=destination,
                    delivery_status="error", delivery_error=error)
            return
        if mode == "none":
            set_job(job_id, delivery_mode=mode, delivery_destination="",
                    delivery_status="not_requested", delivery_error="", delivery_receipt=None)
            return
        if not Path(job["final_path"]).is_file():
            raise RuntimeError("the finished MP4 is missing from its saved location")
        set_job(job_id, delivery_mode=mode, delivery_destination=destination,
                delivery_status="sending", delivery_error="", delivery_receipt=None,
                delivery_attempts=int(job.get("delivery_attempts") or 0) + 1)
        job = job_snapshot(job_id)
        job["delivery_mode"] = mode
        job["delivery_destination"] = destination
        if mode == "email":
            receipt = send_email_delivery(job)
        elif job.get("render_backend") == "aws":
            if not callable(CLOUD_DELIVERY_DISPATCHER):
                raise RuntimeError("Cloud Slack delivery is not connected in this app build.")
            receipt = CLOUD_DELIVERY_DISPATCHER(job)
        else:
            receipt = slack_upload_mp4(
                job["final_path"], destination,
                (CONFIG.get("slack_bot_token") or "").strip(),
                fields.get("caption") or "")
        set_job(job_id, delivery_status="sent", delivery_error="",
                delivery_receipt=receipt or {"provider": mode},
                delivered_at=time.time())
    except SlackDeliveryError as error:
        set_job(job_id, delivery_status="needs_review" if error.uncertain else "error",
                delivery_error=str(error), delivery_receipt=None)
    except Exception as error:
        set_job(job_id, delivery_status="error", delivery_error=str(error),
                delivery_receipt=None)
    finally:
        lock.release()


def queue_delivery(job_id, allow_uncertain_retry=False):
    """Queue one send attempt; duplicate UI clicks cannot launch another send."""
    job = job_snapshot(job_id)
    if not job or job.get("status") != "completed" or not job.get("final_path"):
        raise RuntimeError("delivery is available after the approved video is published")
    status = job.get("delivery_status") or "not_requested"
    if status == "sent":
        return False
    if status in {"queued", "sending"}:
        return False
    fields = job.get("current_fields") or {}
    mode, destination, error = delivery_details(fields)
    if error:
        set_job(job_id, delivery_mode=mode, delivery_destination=destination,
                delivery_status="error", delivery_error=error)
        return False
    if mode == "none":
        set_job(job_id, delivery_mode="none", delivery_destination="",
                delivery_status="not_requested", delivery_error="", delivery_receipt=None)
        return False
    if status == "needs_review" and not allow_uncertain_retry:
        detail = ("the Slack file may already have reached its channel" if mode == "slack"
                  else "the email may already have been sent")
        raise RuntimeError(f"Review the interrupted delivery before retrying; {detail}.")
    if mode == "slack" and job.get("render_backend") == "aws" and job.get("cloud_delivery"):
        import aws_render
        attempt = job["cloud_delivery"]
        state = aws_render.wait_or_poll_render(aws_settings(), attempt, wait_seconds=0)
        if state["status"] in ("running", "dispatching") and not state.get("task_arn"):
            state = aws_render.reconcile_render(aws_settings(), state)
        if state["status"] in ("running", "dispatching") and not state.get("task_arn") and not state.get("result"):
            if not allow_uncertain_retry:
                set_job(job_id, delivery_status="needs_review",
                        delivery_error="AWS dispatch is unconfirmed; inspect the saved attempt before retrying.")
                return False
            set_job(job_id, cloud_delivery=None)
        elif state["status"] in ("failed", "uncertain"):
            if not allow_uncertain_retry:
                set_job(job_id, cloud_delivery=state, delivery_status="needs_review",
                        delivery_error="AWS Slack delivery may have completed; check the channel before retrying.")
                return False
            set_job(job_id, cloud_delivery=None)
        elif state["status"] == "succeeded":
            result = state.get("result") or {}
            set_job(job_id, cloud_delivery=state, delivery_status="sent", delivery_error="",
                    delivery_receipt={"provider": "slack", "file_id": result.get("file_id"),
                                      "channel_id": result.get("channel_id"),
                                      "permalink": (result.get("slack_file") or {}).get("permalink", "")},
                    delivered_at=time.time())
            return False
        else:
            set_job(job_id, cloud_delivery=state)
    set_job(job_id, delivery_mode=mode, delivery_destination=destination,
            delivery_status="queued", delivery_error="", delivery_receipt=None)
    threading.Thread(target=send_job_delivery,
                     args=(job_id, allow_uncertain_retry), daemon=True).start()
    return True


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
    fields = normalize_delivery_fields(job.get("current_fields") or JOB_FORMS.get(job_id) or {})
    delivery_mode, delivery_destination, delivery_error = delivery_details(fields)
    recipient = delivery_destination if delivery_mode == "email" and not delivery_error else ""
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
            final_path=published, current_fields=fields,
            delivery_mode=delivery_mode, delivery_destination=delivery_destination,
            delivery_status=("not_requested" if delivery_mode == "none" else
                             "error" if delivery_error else "awaiting_approval"),
            delivery_error=delivery_error, delivery_receipt=None,
            tracks=[{"file": published, "name": Path(published).name, "video": True}])
    with JOBS_LOCK:
        JOB_FORMS.pop(job_id, None)
        _save_jobs_locked()
    if delivery_mode != "none" and not delivery_error:
        queue_delivery(job_id)


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


def uploaded_image_suffix(data):
    """Identify upload formats the local FFmpeg renderer can reliably consume."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def add_uploaded_image(job_id, filename, data):
    """Add a validated local image as an artwork choice for a paused song."""
    job = job_snapshot(job_id)
    if not job or not job.get("pipeline"):
        raise RuntimeError("that pipeline job no longer exists")
    if job.get("status") != "paused_song":
        raise RuntimeError("an image can be uploaded while the job is waiting for song approval")
    if job.get("stale_song"):
        raise RuntimeError("lyrics or genre changed; resubmit to Suno before adding artwork")
    song = selected_variant(job, "song_variants", "selected_song")
    if not song:
        raise RuntimeError("select a song variant before uploading artwork")
    suffix = uploaded_image_suffix(data)
    if not suffix:
        raise RuntimeError("upload a PNG, JPEG, or WebP image")
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg is required to validate uploaded artwork")

    root = Path(job.get("staging_folder") or pipeline_root("staging") / job_id)
    folder = allocate_unique_dir(root / "image-upload")
    image = folder / ("art" + suffix)
    try:
        image.write_bytes(data)
        image_dimensions(ff, image)
    except Exception:
        try:
            image.unlink(missing_ok=True)
            folder.rmdir()
        except OSError:
            pass
        raise

    form = dict(job.get("current_fields") or JOB_FORMS.get(job_id) or {})
    label = Path(filename or "uploaded image").name[:120]
    variant = {"id": uuid.uuid4().hex, "file": str(image), "created": time.time(),
               "prompt": "", "inputs": form, "song_variant": song.get("id"),
               "source": "upload", "name": label}
    variants = list(job.get("image_variants") or []) + [variant]
    # Uploaded artwork is deliberately held for the same visible image review
    # used by generated variants; it never starts a video behind the operator's back.
    set_job(job_id, image_variants=variants, selected_image=variant["id"],
            image_regenerations=max(0, len(variants) - 1), status="paused_image",
            stage="image", message="uploaded image ready for approval")


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
            video_image_file=str(image_file), cloud_execution=None)
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
            "negativeTags", "recipient", "delivery_mode", "slack_channel_id", "caption",
            "vocalGender", "styleWeight", "weirdnessConstraint"}})
        current = normalize_delivery_fields(current)
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
        validate_display_lyrics(form.get("lyrics", ""), form.get("display_lyrics"))
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
    elif action == "retry_video":
        if job.get("status") != "error" or job.get("stage") != "video":
            raise RuntimeError("video retry is available after a failed video stage")
        JOB_CANCELS[job_id] = threading.Event()
        execution = job.get("cloud_execution")
        if execution:
            import aws_render
            state = aws_render.wait_or_poll_render(aws_settings(), execution, wait_seconds=0)
            if state["status"] in ("running", "dispatching") and not state.get("task_arn"):
                state = aws_render.reconcile_render(aws_settings(), state)
            if state["status"] in ("running", "dispatching") and not state.get("task_arn") and not state.get("result"):
                raise RuntimeError("AWS attempt is unconfirmed; inspect ECS before starting another paid render")
            if state["status"] in ("running", "succeeded"):
                song = selected_variant(job, "song_variants", "selected_song")
                image = selected_variant(job, "image_variants", "selected_image")
                track = dict(song.get("track") or {}) if song else {}
                track["file"] = song.get("file") if song else ""
                track["pipeline_image"] = image.get("file") if image else ""
                set_job(job_id, cloud_execution=state, status="queued", stage="video")
                threading.Thread(target=run_video_job, args=(job_id, track, True), daemon=True).start()
                return
        start_pipeline_video(job_id)
    elif action == "rerender_subtitles":
        start_subtitle_rerender(job_id)
    elif action == "approve_video":
        current = normalize_delivery_fields(current)
        set_job(job_id, current_fields=current)
        finalize_pipeline_job(job_id)
    elif action == "retry_delivery":
        if job.get("status") != "completed" or not job.get("final_path"):
            raise RuntimeError("delivery can be retried after the approved video is published")
        allow_uncertain_retry = bool((fields or {}).get("confirm_uncertain_delivery"))
        current = normalize_delivery_fields(current)
        set_job(job_id, current_fields=current)
        queue_delivery(job_id, allow_uncertain_retry=allow_uncertain_retry)
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
    form = normalize_delivery_fields(form)
    validate_display_lyrics(form.get("lyrics", ""), form.get("display_lyrics"))
    if not (form.get("style") or "").strip() and (CONFIG.get("default_style") or "").strip():
        form["style"] = CONFIG["default_style"].strip()
    job_id = uuid.uuid4().hex
    provider_name = CONFIG.get("provider") or "kie"
    staging_folder = pipeline_root("staging") / job_id
    delivery_mode, delivery_destination, delivery_error = delivery_details(form)
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
            "delivery_mode": delivery_mode,
            "delivery_destination": delivery_destination,
            "delivery_status": "not_requested" if delivery_mode == "none" else "awaiting_approval",
            "delivery_error": delivery_error,
            "delivery_receipt": None,
            "delivery_attempts": 0,
            "render_backend": CONFIG.get("render_backend", "local"),
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
            validate_display_lyrics(form.get("lyrics", ""), form.get("display_lyrics"))
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
        # variant; the provider has no supported output-count parameter.
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
                    data["display_lyrics"] = form.get("display_lyrics") or ""
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
            if (form.get("display_lyrics") or "").strip():
                meta.extend(["", "===DISPLAY LYRICS===", form["display_lyrics"].strip()])
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
    generations, videos, subtitle_rerenders = [], [], []
    with JOBS_LOCK:
        for job_id, job in JOBS.items():
            status = job.get("status")
            if job.get("delivery_status") in ("queued", "sending"):
                if job.get("delivery_mode") == "slack":
                    message = ("the Slack send was interrupted and may already have posted; "
                               "review before retrying")
                else:
                    message = ("the email send was interrupted; check delivery before retrying")
                job.update(delivery_status="needs_review", delivery_error=message)
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
                    if job.get("subtitle_rerendering"):
                        subtitle_rerenders.append(job_id)
                        continue
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
    for job_id in subtitle_rerenders:
        threading.Thread(target=run_subtitle_rerender, args=(job_id, True),
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
    str(Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "arial.ttf"),
    str(Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "segoeui.ttf"),
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


TIMING_TSV_COLUMNS = ("line_id", "word_id", "start", "end", "text")
TIMING_TSV_TIMESTAMP = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{3})$")


def karaoke_groups(aligned, lyrics_text="", aligner_method=None,
                   display_lyrics="", display_warnings=None):
    """Return the renderer's existing grouped ``w``/``s``/``e`` words.

    This deliberately keeps the alignment and fallback decision in one place:
    a timing sidecar changes only timestamps after the selected aligner has
    chosen the text and event grouping.
    """
    alignment = lines_from_lyrics(aligned, lyrics_text, method=aligner_method,
                                  return_result=True)
    groups = alignment["groups"]
    groups = group_lyric_lines(aligned) if groups is None else groups
    return display_karaoke_groups(groups, lyrics_text, display_lyrics,
                                  alignment, display_warnings)


def display_karaoke_groups(groups, lyrics_text, display_lyrics, alignment=None,
                           warnings=None):
    """Replace each timed sung line with its matching display spelling."""
    if not display_lyrics:
        return groups
    spoken = parse_authored_lyrics(lyrics_text)
    shown = parse_authored_lyrics(display_lyrics)
    replacements = {(section["index"], line["index"]): (line["text"], display["text"])
                    for section, display_section in zip(spoken, shown)
                    for line, display in zip(section["lines"], display_section["lines"])}
    diagnostics = (alignment or {}).get("lines") or []
    if diagnostics:
        matches = []
        for group in groups:
            start, end = group[0][0]["s"], group[-1][-1]["e"]
            matches.append(next((line for line in diagnostics
                                 if line["start"] is not None and line["end"] is not None
                                 and abs(line["start"] - start) < 0.03
                                 and abs(line["end"] - end) < 0.03), None))
    else:
        keys = list(replacements)
        matches = ([{"section_index": key[0], "line_index": key[1]} for key in keys]
                   if len(keys) == len(groups) else [None] * len(groups))

    result = []
    for group, line in zip(groups, matches):
        pair = replacements.get((line["section_index"], line["line_index"])) if line else None
        if not pair:
            if warnings is not None:
                warnings.append("one audio line has no matching display line; kept Suno text")
            result.append(group)
            continue
        if pair[0] == pair[1]:
            result.append(group)
            continue
        items = [item for row in group for item in row]
        sung_words, words = pair[0].split(), pair[1].split()
        # Use the Suno spelling for timing boundaries; its pronunciation can
        # have different letters from the word displayed on screen.
        if (len(sung_words) == len(words) and
                _chars(join_words([item["w"] for item in items])) == _chars(pair[0]) and
                all(_chars(word) for word in sung_words + words + [item["w"] for item in items])):
            mapped, index = [], 0
            for sung, word in zip(sung_words, words):
                first, length = index, 0
                while index < len(items) and length < len(_chars(sung)):
                    length += len(_chars(items[index]["w"]))
                    index += 1
                if length != len(_chars(sung)):
                    break
                mapped.append({"w": word, "s": items[first]["s"], "e": items[index - 1]["e"],
                               "parenthetical": any(it.get("parenthetical") for it in items[first:index])})
            if len(mapped) == len(words) and index == len(items):
                result.append([mapped])
                continue
        # ponytail: uncertain word boundaries use one whole-line highlight;
        # add fuzzy token mapping only if real songs need finer timing.
        result.append([[{"w": pair[1], "s": items[0]["s"], "e": items[-1]["e"]}]])
        if warnings is not None:
            warnings.append(f"display line {line['section_index'] + 1}.{line['line_index'] + 1} "
                            "uses one whole-line highlight")
    return result


def safe_display_lyrics(display_lyrics, hybrid):
    """Keep the display lines accepted by stable-ts in their original order."""
    if not display_lyrics:
        return ""
    lines = [line["text"] for section in parse_authored_lyrics(display_lyrics)
             for line in section["lines"]]
    diagnostics = hybrid.get("lines") or []
    if len(lines) != len(diagnostics):
        raise ValueError("stable-ts display lines no longer match the saved lyric sheet")
    return "\n".join(line for line, diagnostic in zip(lines, diagnostics)
                     if not diagnostic.get("hidden"))


def _timing_milliseconds(seconds):
    """Round a non-negative timestamp half-up for the editable TSV."""
    return max(0, int(math.floor(float(seconds) * 1000 + 0.5 + 1e-9)))


def timing_timestamp(seconds):
    """Format an absolute timestamp as HH:MM:SS.mmm without losing Unicode text."""
    return _format_timing_milliseconds(_timing_milliseconds(seconds))


def _format_timing_milliseconds(milliseconds):
    """Format an already-normalized millisecond value for the TSV."""
    hour, milliseconds = divmod(milliseconds, 3_600_000)
    minute, milliseconds = divmod(milliseconds, 60_000)
    second, milliseconds = divmod(milliseconds, 1_000)
    return f"{hour:02d}:{minute:02d}:{second:02d}.{milliseconds:03d}"


def parse_timing_timestamp(value):
    """Parse the TSV's deliberately strict millisecond timestamp format."""
    match = TIMING_TSV_TIMESTAMP.match((value or "").strip())
    if not match:
        raise ValueError("timestamp must use HH:MM:SS.mmm (for example 00:00:26.000)")
    hour, minute, second, millisecond = (int(part) for part in match.groups())
    if minute >= 60 or second >= 60:
        raise ValueError("timestamp has an out-of-range minute or second")
    return hour * 3600 + minute * 60 + second + millisecond / 1000


def timing_sidecar_text(groups):
    """Serialize renderer groups into an editable UTF-8 TSV timing table."""
    from io import StringIO

    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(TIMING_TSV_COLUMNS)
    for line_id, word_id, item, start, end in _timing_sidecar_rows(groups):
        writer.writerow((line_id, word_id, start, end, item["w"]))
    return stream.getvalue()


def _timing_sidecar_rows(groups):
    """Yield TSV-safe timestamps while retaining the original renderer item."""
    for line_id, rows in enumerate(groups, 1):
        previous_end = None
        for word_id, item in enumerate((it for row in rows for it in row), 1):
            # TSV has millisecond precision, while a provider may supply a
            # shorter-than-one-ms span. Normalize only those unrepresentable
            # boundaries so an exported, unedited sidecar is always valid.
            start = _timing_milliseconds(item["s"])
            if previous_end is not None:
                start = max(start, previous_end)
            end = max(start + 1, _timing_milliseconds(item["e"]))
            yield (line_id, word_id, item, _format_timing_milliseconds(start),
                   _format_timing_milliseconds(end))
            previous_end = end


def export_timing_sidecar(path, groups):
    """Write the current renderer words as a human-editable timing sidecar."""
    atomic_write_text(path, timing_sidecar_text(groups))


def apply_timing_sidecar(path, groups):
    """Validate and apply a TSV timing sidecar to already-aligned groups.

    IDs and text are a strict snapshot check, so an old sidecar cannot silently
    land on a different repeated lyric.  Timings are validated per rendered
    event; independent dialogue events may still overlap exactly as before.
    """
    path = Path(path)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read timing sidecar: {error}") from error
    return apply_timing_sidecar_text(source, groups)


def apply_timing_sidecar_text(source, groups):
    """Validate TSV text from the in-app editor against renderer groups."""
    if not isinstance(source, str):
        raise ValueError("timing sidecar is not text")
    try:
        rows = list(csv.reader(source.splitlines(), delimiter="\t"))
    except csv.Error as error:
        raise ValueError(f"cannot parse timing sidecar: {error}") from error
    if not rows or tuple(rows[0]) != TIMING_TSV_COLUMNS:
        raise ValueError("sidecar line 1: expected header " + "\t".join(TIMING_TSV_COLUMNS))

    expected = {(line_id, word_id): {"item": item, "start": start, "end": end}
                for line_id, word_id, item, start, end in _timing_sidecar_rows(groups)}
    received, parsed = set(), {}
    for number, row in enumerate(rows[1:], 2):
        if len(row) != len(TIMING_TSV_COLUMNS):
            raise ValueError(f"sidecar line {number}: expected 5 tab-separated columns")
        line_value, word_value, start_value, end_value, text_value = row
        if not line_value.isdecimal() or not word_value.isdecimal() or \
                int(line_value) < 1 or int(word_value) < 1:
            raise ValueError(f"sidecar line {number}: line_id and word_id must be positive integers")
        key = (int(line_value), int(word_value))
        if key in received:
            raise ValueError(f"sidecar line {number}: duplicate line_id {key[0]} word_id {key[1]}")
        if key not in expected:
            raise ValueError(f"sidecar line {number}: unknown line_id {key[0]} word_id {key[1]}")
        expected_item = expected[key]["item"]
        if text_value != expected_item["w"]:
            raise ValueError(f"sidecar line {number}: text does not match line_id {key[0]} word_id {key[1]}")
        try:
            parsed_start = parse_timing_timestamp(start_value)
            parsed_end = parse_timing_timestamp(end_value)
        except ValueError as error:
            raise ValueError(f"sidecar line {number}: {error}") from error
        changed = (start_value != expected[key]["start"] or
                   end_value != expected[key]["end"])
        if parsed_end <= parsed_start:
            raise ValueError(f"sidecar line {number}: end must be after start")
        received.add(key)
        # Retain original high-precision timings for untouched words. This is
        # what prevents an edit to one row from perturbing later word starts.
        parsed[key] = (parsed_start if start_value != expected[key]["start"]
                       else expected_item["s"],
                       parsed_end if end_value != expected[key]["end"]
                       else expected_item["e"], number, changed)
    missing = sorted(set(expected) - received)
    if missing:
        line_id, word_id = missing[0]
        raise ValueError(f"sidecar is missing line_id {line_id} word_id {word_id}")

    revised = []
    previous_line_start = None
    for line_id, event_rows in enumerate(groups, 1):
        revised_rows, previous_end, previous_changed = [], None, False
        word_id = 0
        for row in event_rows:
            revised_row = []
            for item in row:
                word_id += 1
                start, end, number, changed = parsed[(line_id, word_id)]
                if previous_end is not None and start < previous_end and \
                        (changed or previous_changed):
                    raise ValueError(f"sidecar line {number}: start overlaps the preceding word")
                clone = dict(item)
                clone.update({"s": start, "e": end})
                revised_row.append(clone)
                previous_end = end
                previous_changed = changed
            revised_rows.append(revised_row)
        line_start = revised_rows[0][0]["s"]
        line_changed = parsed[(line_id, 1)][3]
        if previous_line_start is not None and line_start < previous_line_start[0] and \
                (line_changed or previous_line_start[1]):
            raise ValueError(f"sidecar line {parsed[(line_id, 1)][2]}: line start moves before the prior line")
        previous_line_start = (line_start, line_changed)
        revised.append(revised_rows)
    return revised


def _karaoke_centiseconds(seconds):
    """Quantize an absolute timestamp once, avoiding cumulative round-off drift."""
    return max(0, int(math.floor(float(seconds) * 100 + 0.5 + 1e-9)))


def ass_time_centiseconds(centiseconds):
    """Format an already-quantized ASS timestamp without a second rounding step."""
    hour, centiseconds = divmod(max(0, int(centiseconds)), 360_000)
    minute, centiseconds = divmod(centiseconds, 6_000)
    second, centiseconds = divmod(centiseconds, 100)
    return f"{hour:d}:{minute:02d}:{second:02d}.{centiseconds:02d}"


def build_karaoke_ass(aligned, font="Helvetica", hi="&H00A6D322", lo="&H00C8C8C8",
                      lead=0.18, tail=0.45, banner=None, lyrics_text="",
                      aligner_method=None, paren_hi="&H0042B9F5",
                      paren_lo="&H0080BFE0", timing_groups=None,
                      display_lyrics=""):
    """ASS with \\kf karaoke fills. Colours are &HAABBGGRR - BGR, not RGB.

    banner=(title, subtitle) draws the title through libass instead of
    drawtext, for ffmpeg builds without freetype."""
    groups = timing_groups if timing_groups is not None else \
        karaoke_groups(aligned, lyrics_text, aligner_method,
                       display_lyrics=display_lyrics)
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
        # The automatic baseline retains its historical segment rounding. A
        # sidecar uses absolute centisecond targets, correcting every rounding
        # discrepancy at that word's local gap instead of carrying it forward.
        use_absolute_targets = timing_groups is not None
        elapsed_cs = _karaoke_centiseconds(start) if use_absolute_targets else None
        paren_active = False
        for ri, row in enumerate(rows):
            if ri:
                parts.append("\\N")
            first_in_row = True
            for it in row:
                if use_absolute_targets:
                    target_start = _karaoke_centiseconds(it["s"])
                    target_end = _karaoke_centiseconds(it["e"])
                    hold = max(0, target_start - elapsed_cs)
                    duration = max(1, target_end - target_start)
                    elapsed_cs = target_start + duration
                else:
                    hold = max(0, int(round((it["s"] - prev) * 100)))
                    duration = max(1, int(round((it["e"] - it["s"]) * 100)))
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
                parts.append("{\\kf%d}%s" % (duration, ass_escape(it["w"])))
                plain += it["w"]
                prev = it["e"]
        event_start = (ass_time_centiseconds(_karaoke_centiseconds(start))
                       if use_absolute_targets else ass_time(start))
        event_end = (ass_time_centiseconds(_karaoke_centiseconds(end))
                     if use_absolute_targets else ass_time(end))
        out.append(f"Dialogue: 0,{event_start},{event_end},Now,,0,0,0,,"
                   f"{{\\fad(140,140)}}{''.join(parts)}")
        rendered += 1
    return "\n".join(out) + "\n", rendered


def ass_escape(s):
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


MAX_SUBTITLE_EDIT_BYTES = 1024 * 1024
ASS_DIALOGUE_RE = re.compile(
    r"^Dialogue:\s*[^,]*,\s*([^,]+),\s*([^,]+),", re.IGNORECASE)
ASS_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")


def ass_seconds(value):
    """Parse the ASS H:MM:SS.cc format used by the local renderer."""
    match = ASS_TIME_RE.match((value or "").strip())
    if not match:
        raise ValueError(f"invalid ASS timestamp '{value}'")
    hour, minute, second, centisecond = (int(part) for part in match.groups())
    if minute >= 60 or second >= 60:
        raise ValueError(f"invalid ASS timestamp '{value}'")
    return hour * 3600 + minute * 60 + second + centisecond / 100


def validate_editable_ass(text):
    """Accept a conservative, renderer-compatible subset of an ASS document."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("subtitle text is empty")
    if len(text.encode("utf-8")) > MAX_SUBTITLE_EDIT_BYTES:
        raise ValueError("subtitle text exceeds the 1 MB limit")
    if "\x00" in text or "[Script Info]" not in text or "[Events]" not in text:
        raise ValueError("the ASS [Script Info] and [Events] sections are required")
    dialogues = 0
    for number, line in enumerate(text.splitlines(), 1):
        match = ASS_DIALOGUE_RE.match(line)
        if not match:
            continue
        start, end = ass_seconds(match.group(1)), ass_seconds(match.group(2))
        if end <= start:
            raise ValueError(f"Dialogue line {number} ends before it starts")
        dialogues += 1
    if not dialogues:
        raise ValueError("the ASS file has no Dialogue events")
    return dialogues


ASS_KF_TAG_RE = re.compile(r"\{\\kf(\d+)\}")


def _ass_dialogue_fields(line):
    """Return ASS dialogue fields, including the unparsed Text field."""
    if not line.startswith("Dialogue:"):
        return None
    fields = line[len("Dialogue:"):].lstrip().split(",", 9)
    return fields if len(fields) == 10 else None


def _karaoke_chunk_text(chunk):
    """Visible word text following one karaoke tag, minus ASS control codes."""
    visible = re.sub(r"\{[^}]*\}", "", chunk)
    return visible.replace("\\N", " ").replace("\u200b", "").strip()


def karaoke_groups_from_ass(ass_text):
    """Convert existing generated ASS to editable per-word timing groups.

    This is the migration path for a video that was already waiting for
    approval before the TSV sidecar existed.  It reads only the existing
    ``\\kf`` timeline; styles, fades, and dialogue boundaries remain in ASS.
    """
    groups = []
    for number, line in enumerate((ass_text or "").splitlines(), 1):
        fields = _ass_dialogue_fields(line)
        if not fields:
            continue
        tags = list(ASS_KF_TAG_RE.finditer(fields[9]))
        if not tags:
            continue
        try:
            clock = _karaoke_centiseconds(ass_seconds(fields[1]))
        except ValueError as error:
            raise ValueError(f"ASS Dialogue line {number}: {error}") from error
        words = []
        for index, tag in enumerate(tags):
            chunk_end = tags[index + 1].start() if index + 1 < len(tags) else len(fields[9])
            chunk = fields[9][tag.end():chunk_end]
            duration = int(tag.group(1))
            word = _karaoke_chunk_text(chunk)
            if word:
                words.append({"w": word, "s": clock / 100, "e": (clock + duration) / 100})
            clock += duration
        if words:
            groups.append([words])
    if not groups:
        raise ValueError("the ASS file has no word-level karaoke timing")
    return groups


def render_ass_timing_sidecar(ass_text, groups):
    """Apply absolute TSV timing groups while preserving the original ASS text.

    Only karaoke durations and zero-width gap carriers are rebuilt. Dialogue
    fields, fades, colours, style changes, and visible lyric chunks are left
    in place, so an in-progress video retains its approved appearance.
    """
    output, group_index = [], 0
    for line in (ass_text or "").splitlines(keepends=True):
        ending = "\n" if line.endswith("\n") else ""
        content = line[:-1] if ending else line
        fields = _ass_dialogue_fields(content)
        tags = list(ASS_KF_TAG_RE.finditer(fields[9])) if fields else []
        words = []
        for index, tag in enumerate(tags):
            chunk_end = tags[index + 1].start() if index + 1 < len(tags) else len(fields[9])
            chunk = fields[9][tag.end():chunk_end]
            if _karaoke_chunk_text(chunk):
                words.append((tag, chunk))
        if not words:
            output.append(line)
            continue
        if group_index >= len(groups):
            raise ValueError("timing sidecar has fewer lyric events than the ASS file")
        wanted = [item for row in groups[group_index] for item in row]
        if len(words) != len(wanted):
            raise ValueError(f"timing sidecar line {group_index + 2}: word count no longer matches the ASS event")
        try:
            clock = _karaoke_centiseconds(ass_seconds(fields[1]))
        except ValueError as error:
            raise ValueError(f"ASS Dialogue event {group_index + 1}: {error}") from error
        pieces, cursor, word_index, pending = [fields[9][:tags[0].start()]], tags[0].start(), 0, ""
        for index, tag in enumerate(tags):
            chunk_end = tags[index + 1].start() if index + 1 < len(tags) else len(fields[9])
            chunk = fields[9][tag.end():chunk_end]
            pieces.append(fields[9][cursor:tag.start()]) if cursor < tag.start() else None
            if _karaoke_chunk_text(chunk):
                item = wanted[word_index]
                target_start = _karaoke_centiseconds(item["s"])
                target_end = _karaoke_centiseconds(item["e"])
                gap = max(0, target_start - clock)
                if gap:
                    # Reuse the original inter-word space when there is one:
                    # libass needs a glyph after a timing-only tag. A zero-width
                    # carrier keeps contraction/leading gaps intact.
                    pieces.append("{\\kf%d}%s" % (gap, pending or "\u200b"))
                    pending = ""
                elif pending:
                    pieces.append(pending)
                    pending = ""
                duration = max(1, target_end - target_start)
                pieces.append("{\\kf%d}%s" % (duration, chunk))
                clock = max(clock, target_start) + duration
                word_index += 1
            else:
                # Retain the original visible spacing/control codes but remove
                # its obsolete timing tag; the absolute gap above owns timing.
                pending += chunk
            cursor = chunk_end
        pieces.append(pending)
        if cursor < len(fields[9]):
            pieces.append(fields[9][cursor:])
        fields[9] = "".join(pieces)
        output.append("Dialogue: " + ",".join(fields) + ending)
        group_index += 1
    if group_index != len(groups):
        raise ValueError("timing sidecar has more lyric events than the ASS file")
    return "".join(output)


def subtitle_timing_path(generated_ass):
    return Path(generated_ass).with_suffix(".timings.tsv")


def subtitle_timing_text(job):
    """Load or convert a review job's ASS into the human-readable TSV."""
    generated, override = subtitle_paths(job)
    sidecar = subtitle_timing_path(generated)
    if sidecar.is_file():
        return sidecar.read_text(encoding="utf-8"), sidecar, bool(override.is_file())
    source = override if override.is_file() else generated
    groups = karaoke_groups_from_ass(source.read_text(encoding="utf-8"))
    text = timing_sidecar_text(groups)
    atomic_write_text(sidecar, text)
    return text, sidecar, bool(override.is_file())


def save_subtitle_timing_text(job, text):
    """Validate TSV and materialize an ASS override for immediate re-rendering."""
    generated, override = subtitle_paths(job)
    sources = [generated] + ([override] if override.is_file() else [])
    last_error = None
    for source in sources:
        try:
            baseline = source.read_text(encoding="utf-8")
            groups = karaoke_groups_from_ass(baseline)
            revised = apply_timing_sidecar_text(text, groups)
            rendered = render_ass_timing_sidecar(baseline, revised)
            validate_editable_ass(rendered)
            atomic_write_text(subtitle_timing_path(generated), timing_sidecar_text(revised))
            atomic_write_text(override, rendered)
            return sum(len(row) for event in revised for row in event)
        except (OSError, ValueError) as error:
            last_error = error
    raise ValueError(str(last_error or "could not apply timing sidecar"))


def subtitle_paths(job):
    """Return only job-owned subtitle paths; callers never supply filesystem paths."""
    generated = Path(job.get("subtitle_generated_path") or "")
    override = Path(job.get("subtitle_override_path") or "")
    if not generated.is_file() or not override.name:
        raise RuntimeError("the generated subtitle source is no longer available")
    if override.parent != generated.parent:
        raise RuntimeError("the subtitle override path is invalid")
    return generated, override


def effective_subtitle_path(job):
    generated, override = subtitle_paths(job)
    return override if override.is_file() else generated


def recover_subtitle_review_metadata(job_id):
    """Upgrade a pre-editor paused review when its local ASS inputs remain."""
    job = job_snapshot(job_id)
    if not job or not job.get("pipeline") or job.get("status") != "paused_video":
        return False
    existing = job.get("subtitle_generated_path")
    if existing and Path(existing).is_file():
        return True
    song = selected_variant(job, "song_variants", "selected_song") or {}
    track = dict(song.get("track") or {})
    mp3 = Path(song.get("file") or track.get("file") or "")
    root = Path(job.get("staging_folder") or "")
    candidates = [mp3.with_suffix(".ass")] if mp3.name else []
    if root.is_dir():
        # Old review jobs did not journal their ASS path. Search only their
        # own staging folder, never a caller-provided or global filesystem path.
        candidates.extend(sorted(root.rglob("*.ass")))
    generated = next((path for path in candidates if path.is_file()), None)
    if not generated:
        return False
    try:
        validate_editable_ass(generated.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not mp3.is_file():
        # The historical video path and ASS location share the song folder.
        inferred = generated.with_suffix(".mp3")
        if inferred.is_file():
            mp3 = inferred
    background = mp3.parent / f"{mp3.stem} - background.png" if mp3.name else None
    if not mp3.is_file() or not background or not background.is_file():
        return False
    focus = mp3.parent / f"{mp3.stem} - lyric focus background.png"
    settings = dict(job.get("subtitle_render_settings") or {})
    settings.setdefault("height", int(CONFIG.get("video_height") or 1080))
    settings.setdefault("fps", int(CONFIG.get("video_fps") or 30))
    settings.setdefault("visualizer", CONFIG.get("visualizer") or "bars")
    settings.setdefault("shimmer", bool(CONFIG.get("shimmer", True)))
    settings.setdefault("interlude_mode", bool(CONFIG.get("interlude_mode", True)))
    settings.setdefault("lyric_focus_band", bool(CONFIG.get("lyric_focus_band", True)))
    settings.setdefault("focus_opacity", 0.90)
    settings.setdefault("crf", int(CONFIG.get("video_crf") or 21))
    override = generated.with_name(generated.stem + ".edited.ass")
    set_job(job_id,
            subtitle_generated_path=str(generated), subtitle_override_path=str(override),
            subtitle_effective_path=str(override if override.is_file() else generated),
            subtitle_audio_path=str(mp3), subtitle_background_path=str(background),
            subtitle_focus_background_path=str(focus if focus.is_file() else ""),
            subtitle_basename=safe_name(Path(job.get("video_path") or mp3.stem).stem),
            subtitle_accent=palette_for(job.get("title") or "")[2],
            subtitle_render_settings=settings,
            subtitle_revision=int(job.get("subtitle_revision") or 0),
            message="video ready for approval; manual subtitle timing is available")
    return True


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
                          lyrics_text="", display_lyrics=""):
    """
    Lyrics without libass: one drawtext per line, gated by `enable=between()`.

    No per-word karaoke sweep - drawtext can't fill mid-word - but the current
    line appears and leaves on the beat, and the next line previews faintly.
    Used when ffmpeg has freetype but no libass.
    """
    groups = karaoke_groups(aligned, lyrics_text, display_lyrics=display_lyrics)
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


MAX_VIDEO_BYTES = 30_000_000
VIDEO_SIZE_HEADROOM = 0.95


def video_bitrate_budget(duration, max_bytes=MAX_VIDEO_BYTES,
                         headroom=VIDEO_SIZE_HEADROOM):
    """Return video/audio kbps that keep a complete MP4 below ``max_bytes``.

    The payload budget leaves five percent for MP4 tables, encoder variance,
    and fast-start metadata. Short videos retain the historical 2500/192 kbps
    ceilings; longer videos give up video bitrate first, then audio bitrate.
    """
    try:
        duration = float(duration)
        max_bytes = int(max_bytes)
        headroom = float(headroom)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 or max_bytes <= 0:
        return 2500, 192
    total_kbps = int(max_bytes * 8 * max(0.1, min(1.0, headroom)) /
                     (duration * 1000))
    if total_kbps < 96:
        raise RuntimeError(
            "audio is too long to make a complete video under the 30 MB limit")
    if total_kbps >= 500:
        audio_kbps = 192
    elif total_kbps >= 350:
        audio_kbps = 128
    elif total_kbps >= 220:
        audio_kbps = 96
    else:
        audio_kbps = 48
    video_kbps = total_kbps - audio_kbps
    if video_kbps < 64:
        video_kbps = 64
        audio_kbps = total_kbps - video_kbps
    return min(2500, video_kbps), max(32, audio_kbps)


def enforce_video_size_limit(ff, video_path, duration,
                             max_bytes=MAX_VIDEO_BYTES,
                             progress_callback=None, process_callback=None):
    """Recompress an exceptional oversize render, or refuse to publish it."""
    video_path = Path(video_path)
    if not video_path.is_file() or video_path.stat().st_size <= max_bytes:
        return video_path
    duration = duration or audio_duration(ff, video_path)
    if not duration:
        raise RuntimeError("could not verify a duration for the 30 MB video limit")

    # A capped-CRF encode should already land under its 95% payload budget.
    # These deeper fallbacks cover muxer/encoder variance without making every
    # normal render pay for a second generation of video encoding.
    last_size = video_path.stat().st_size
    for fraction in (0.86, 0.74):
        temp_path = video_path.with_name(
            f".{video_path.name}.{uuid.uuid4().hex}.size-cap.mp4")
        video_kbps, audio_kbps = video_bitrate_budget(
            duration, max_bytes=int(max_bytes * fraction), headroom=1.0)
        args = [
            "-i", str(video_path), "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "medium",
            "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k",
            "-bufsize", f"{max(256, video_kbps * 2)}k",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
            "-movflags", "+faststart", "-f", "mp4", str(temp_path),
        ]
        try:
            print(f"[ffmpeg] rendered video is {last_size / 1e6:.1f} MB; "
                  "applying the 30 MB delivery cap")
            run_ffmpeg(ff, args, "video size cap",
                       progress_callback=progress_callback, duration=duration,
                       process_callback=process_callback)
            last_size = temp_path.stat().st_size
            if last_size <= max_bytes:
                temp_path.replace(video_path)
                return video_path
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass
    raise RuntimeError(
        f"video remained {last_size / 1e6:.1f} MB after applying the 30 MB limit")


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
            "[still][spark]blend=c0_mode=addition:c0_opacity=0.70:"
            "c1_mode=normal:c2_mode=normal[shimmer]"
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
            f"color=white:s=2x{band_h}:r={fps},format=gray,"
            f"geq=lum='clip(255*{alpha:.3f}*({spatial})*({fade}),0,255)',"
            f"scale={w}:{band_h}:flags=neighbor"
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
    video_kbps, audio_kbps = video_bitrate_budget(dur)
    args += ["-i", str(mp3),
            "-filter_complex", ";".join(chains),
            "-map", f"[{last}]", "-map", amap,
            # A near-static frame compresses enormously better with
            # tune=stillimage. Shimmer is deliberately sparse and subtle.
            "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
            "-crf", str(int(crf)), "-maxrate", f"{video_kbps}k",
            "-bufsize", f"{max(256, video_kbps * 2)}k",
            "-pix_fmt", "yuv420p", "-r", str(int(fps)),
            "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart"]
    # Explicit -t, else the looped still image encodes forever.
    args += (["-t", f"{dur:.3f}"] if dur else ["-shortest"])
    # The recovery file deliberately ends in .part so folder watchers cannot
    # mistake it for a finished MP4; specify the muxer instead of inferring it.
    args += ["-f", "mp4", str(out_mp4)]
    run_ffmpeg(ff, args, "video render", progress_callback=progress_callback,
               duration=dur, process_callback=process_callback)
    try:
        enforce_video_size_limit(
            ff, out_mp4, dur, progress_callback=progress_callback,
            process_callback=process_callback)
    except Exception:
        # Never leave an oversize path looking like a finished deliverable.
        try:
            Path(out_mp4).unlink()
        except OSError:
            pass
        raise
    return out_mp4


# Song, image, and encoder work share the configured external-work budget.
# Gate-paused jobs never acquire it.
VIDEO_SLOTS = _slots


def render_video_aws(job_id, assets, settings, destination, journal_key="cloud_execution",
                     release_slot=None):
    """Run or reconcile one paid attempt, then verify its local MP4 download."""
    import aws_render
    config = aws_settings()
    aws_render._config(config)
    job = job_snapshot(job_id)
    execution = job.get(journal_key)
    fresh_attempt = not execution
    if not execution:
        attempt = uuid.uuid4().hex
        execution = {
            "job_id": job_id, "attempt_id": attempt, "status": "dispatching",
            "result_uri": f"s3://{config['bucket']}/render-results/{job_id}/{attempt}/result.json",
        }
        set_job(job_id, **{journal_key: execution})
    else:
        state = aws_render.wait_or_poll_render(config, execution, wait_seconds=0)
        if state["status"] == "succeeded":
            aws_render.download_verified(config, state, destination)
            set_job(job_id, **{journal_key: state})
            return state
        if state["status"] != "running":
            raise RuntimeError((state.get("result") or {}).get("error") or
                               "AWS render attempt failed; start an explicit new attempt")
    if not execution.get("task_arn"):
        execution = aws_render.reconcile_render(config, execution)
        if not execution.get("task_arn") and not execution.get("result"):
            if not fresh_attempt:
                raise RuntimeError("AWS dispatch is unconfirmed; inspect the saved attempt before retrying")
            try:
                execution = aws_render.dispatch_render(
                    config, job_id, execution["attempt_id"], assets, settings)
            except Exception:
                execution = aws_render.reconcile_render(config, execution)
                if not execution.get("task_arn") and not execution.get("result"):
                    raise
        set_job(job_id, **{journal_key: execution})
    if release_slot:
        release_slot()
    set_job(job_id, phase="render", progress=None, message="encoding video on AWS")
    while True:
        if JOB_CANCELS.setdefault(job_id, threading.Event()).is_set():
            aws_render.cancel_render(config, execution)
            raise InterruptedError("cloud video cancelled")
        state = aws_render.wait_or_poll_render(config, execution, wait_seconds=15)
        set_job(job_id, **{journal_key: state})
        if state["status"] == "running":
            continue
        if state["status"] != "succeeded":
            raise RuntimeError((state.get("result") or {}).get("error") or
                               "AWS render ended without a successful result")
        aws_render.download_verified(config, state, destination)
        return state


def complete_video_job(job_id, out, folder, pipeline):
    out = Path(out)
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
                encoder_pid=None, message=f"done - {size:.1f} MB",
                tracks=[{"file": str(out), "name": out.name, "video": True}],
                folder=str(folder))
        with JOBS_LOCK:
            JOB_FORMS.pop(job_id, None)
            _save_jobs_locked()


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
            raise RuntimeError("FFmpeg is missing. Install it, then see SETUP.md to check video tools.")
        mp3 = Path(track["file"])
        if not mp3.exists():
            raise RuntimeError("the audio file is missing - was it moved?")
        folder = mp3.parent
        expected_duration = audio_duration(ff, mp3)
        with JOBS_LOCK:
            saved_job = dict(JOBS.get(job_id) or {})
        pipeline = bool(saved_job.get("pipeline"))
        backend = saved_job.get("render_backend") or "local"
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
            complete_video_job(job_id, out, folder, pipeline)
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
        aligned, lyrics_text, display_lyrics_text = [], "", ""
        if words_path.exists():
            try:
                cached = json.loads(words_path.read_text())
                aligned = cached.get("alignedWords") or []
                lyrics_text = cached.get("lyrics") or ""
                display_lyrics_text = cached.get("display_lyrics") or ""
                log(f"using cached word timings ({len(aligned)} words)")
            except Exception:
                aligned = []
        if not lyrics_text or not display_lyrics_text:
            # older songs: recover the lyrics from the .txt sidecar
            for cand in sorted(folder.glob("*.txt")):
                try:
                    body = cand.read_text(encoding="utf-8")
                    if "-" * 20 in body:
                        saved = body.split("-" * 20, 1)[1].strip()
                        spoken, marker, shown = saved.partition("\n===DISPLAY LYRICS===\n")
                        lyrics_text = lyrics_text or spoken.strip()
                        display_lyrics_text = display_lyrics_text or (shown.strip() if marker else "")
                        break
                except Exception:
                    pass
        lyrics_text = lyrics_text or fields.get("lyrics") or ""
        display_lyrics_text = display_lyrics_text or fields.get("display_lyrics") or ""
        if not aligned and track.get("suno_id") and track.get("task_id"):
            log("fetching word-level lyric timings")
            prov = make_provider(CONFIG, track.get("provider"))
            if not hasattr(prov, "timestamps"):
                raise RuntimeError(f"{prov.label} has no lyric-alignment endpoint")
            data = prov.timestamps(track["task_id"], track["suno_id"])
            data["lyrics"] = lyrics_text
            data["display_lyrics"] = display_lyrics_text
            words_path.write_text(json.dumps(data))
            aligned = data["alignedWords"]
            log(f"got {len(aligned)} aligned words")

        render_lyrics_text = lyrics_text
        render_display_text = display_lyrics_text
        if CONFIG.get("lyric_aligner") == "stable-ts-hybrid" and lyrics_text:
            try:
                hybrid = build_stable_ts_hybrid(
                    mp3, lyrics_text, aligned, ff, scratch_dir=scratch, log=log)
                safe_display = safe_display_lyrics(display_lyrics_text, hybrid)
                aligned = hybrid["alignedWords"]
                # Align only against accepted authored lines. This preserves
                # long exact line breaks without forcing a deliberately hidden
                # line back into the output.
                render_lyrics_text = hybrid.get("safe_lyrics") or ""
                render_display_text = safe_display
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
                # Keep editable absolute timestamps beside the generated ASS.
                # The baseline renderer stays untouched unless an existing TSV
                # validates against this exact aligned word snapshot.
                timing_sidecar_path = folder / f"{mp3.stem}.timings.tsv"
                display_warnings = []
                baseline_groups = karaoke_groups(
                    aligned, render_lyrics_text, display_lyrics=render_display_text,
                    display_warnings=display_warnings)
                for warning in display_warnings:
                    log(warning)
                timing_override = None
                timing_sidecar_edited = False
                if timing_sidecar_path.is_file():
                    try:
                        timing_override = apply_timing_sidecar(timing_sidecar_path,
                                                               baseline_groups)
                        timing_sidecar_edited = (
                            timing_sidecar_text(timing_override) !=
                            timing_sidecar_text(baseline_groups))
                        log(f"applied word timings from {timing_sidecar_path.name}")
                    except ValueError as error:
                        raise RuntimeError(f"invalid timing sidecar {timing_sidecar_path.name}: {error}") from error
                else:
                    export_timing_sidecar(timing_sidecar_path, baseline_groups)
                    log(f"wrote editable word timings to {timing_sidecar_path.name}")
                ass_text, nlines = build_karaoke_ass(
                    aligned, font=ass_font_name(), lyrics_text=render_lyrics_text,
                    display_lyrics=render_display_text,
                    banner=None if (drew_title or not title.strip())
                           else (title, subtitle), timing_groups=timing_override)
                if CONFIG.get("lyric_aligner") == "stable-ts-hybrid":
                    log("line breaks and timings taken from local stable-ts hybrid")
                elif render_lyrics_text:
                    log("line breaks taken from the submitted lyrics")
                # Keep the automatic baseline separate from any operator
                # correction. Preserve the existing .ass sidecar name; the
                # selected aligner remains its only writer, while a manual
                # change can never overwrite that baseline.
                generated_ass_path = folder / f"{mp3.stem}.ass"
                override_ass_path = folder / f"{mp3.stem}.edited.ass"
                atomic_write_text(generated_ass_path, ass_text)
                ass_path = (generated_ass_path if timing_sidecar_edited
                            else override_ass_path if override_ass_path.is_file()
                            else generated_ass_path)
                if timing_sidecar_edited and override_ass_path.is_file():
                    log("edited word-timing TSV takes precedence over the raw ASS override")
                if ass_path == override_ass_path:
                    try:
                        validate_editable_ass(ass_path.read_text(encoding="utf-8"))
                        log("using saved manual subtitle timing")
                    except Exception as error:
                        ass_path = generated_ass_path
                        set_job(job_id, note=f"ignored invalid subtitle override: {str(error)[:140]}")
                log(f"timed {nlines} lyric lines (word-level karaoke)")
            elif "drawtext" in have:
                dt_chain, dt_files, nlines = build_drawtext_lyrics(
                    aligned, int(vh * 16 / 9), vh, find_font(), scratch,
                    lyrics_text=render_lyrics_text, display_lyrics=render_display_text)
                log(f"timed {nlines} lyric lines (no libass - line-level, "
                    f"no word sweep)")
            else:
                raise RuntimeError(
                    "This ffmpeg has neither 'subtitles' (libass) nor 'drawtext' "
                    "(freetype), so no lyrics can be drawn. See SETUP.md for a compatible build.")
        else:
            log("no lyric timings - rendering a visualiser-only video")

        if ass_path:
            # Save only server-created paths in the journal.  The browser can
            # ask for this source by job id, but cannot select an arbitrary
            # local file to read or overwrite.
            set_job(job_id,
                    subtitle_generated_path=str(generated_ass_path),
                    subtitle_override_path=str(override_ass_path),
                    subtitle_effective_path=str(ass_path),
                    subtitle_audio_path=str(mp3),
                    subtitle_background_path=str(bg),
                    subtitle_focus_background_path=str(focus_bg or ""),
                    subtitle_basename=video_basename,
                    subtitle_revision=int(saved_job.get("subtitle_revision") or 0))

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

            if ass_path:
                set_job(job_id, subtitle_effective_path=str(ass_path),
                        subtitle_accent=accent,
                        subtitle_render_settings={
                            "height": vh, "fps": int(CONFIG.get("video_fps") or 30),
                            "visualizer": vis_mode,
                            "shimmer": bool(CONFIG.get("shimmer", True)),
                            "interlude_mode": bool(CONFIG.get("interlude_mode", True)),
                            "lyric_focus_band": bool(CONFIG.get("lyric_focus_band", True)),
                            "focus_opacity": 0.90,
                            "crf": int(CONFIG.get("video_crf") or 21),
                        })
            if backend == "aws":
                def release_for_cloud():
                    nonlocal acquired
                    if acquired:
                        VIDEO_SLOTS.release()
                        acquired = False

                render_video_aws(job_id, {
                    "audio": mp3, "background": bg, "subtitles": ass_path,
                    "focus_background": focus_bg,
                }, {
                    "accent": accent, "height": vh,
                    "fps": int(CONFIG.get("video_fps") or 30), "vis": vis_mode,
                    "shimmer": bool(CONFIG.get("shimmer", True)),
                    "interlude_mode": bool(CONFIG.get("interlude_mode", True)),
                    "lyric_focus_band": bool(CONFIG.get("lyric_focus_band", True)),
                    "focus_opacity": 0.90, "crf": int(CONFIG.get("video_crf") or 21),
                    "drawtext_chain": dt_chain,
                }, part, release_slot=release_for_cloud)
                if not completed_video_matches(ff, part, expected_duration):
                    raise RuntimeError("downloaded AWS video failed playback or duration verification")
            else:
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
        complete_video_job(job_id, out, folder, pipeline)
        log(f"finished -> {out.name}")
    except Exception as e:
        if part:
            try:
                part.unlink()
            except OSError:
                pass
        if out and not out.is_file():
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
            "render_backend": CONFIG.get("render_backend", "local"),
        }
        JOB_FORMS[job_id] = {"track": dict(track)}
        _save_jobs_locked()
    threading.Thread(target=run_video_job, args=(job_id, track), daemon=True).start()
    return job_id


def run_subtitle_rerender(job_id, recovering=False):
    """Re-encode a gated video from saved render inputs and an edited ASS only."""
    acquired = False
    part = None
    try:
        if not VIDEO_SLOTS.acquire(blocking=False):
            set_job(job_id, status="queued", phase="render",
                    message="waiting for the video renderer")
            VIDEO_SLOTS.acquire()
        acquired = True
        job = job_snapshot(job_id)
        if not job.get("pipeline") or job.get("stage") != "video":
            raise RuntimeError("subtitle re-rendering is available only during video review")
        ff = find_ffmpeg()
        if not ff:
            raise RuntimeError("FFmpeg is missing. Install it, then see SETUP.md to check video tools.")
        mp3 = Path(job.get("subtitle_audio_path") or "")
        bg = Path(job.get("subtitle_background_path") or "")
        focus_raw = job.get("subtitle_focus_background_path") or ""
        focus_bg = Path(focus_raw) if focus_raw else None
        ass_path = effective_subtitle_path(job)
        if not mp3.is_file() or not bg.is_file():
            raise RuntimeError("saved audio or background is missing; generate the video again")
        if focus_bg and not focus_bg.is_file():
            focus_bg = None
        validate_editable_ass(ass_path.read_text(encoding="utf-8"))
        settings = dict(job.get("subtitle_render_settings") or {})
        revision = max(1, int(job.get("subtitle_revision") or 0))
        basename = safe_name(job.get("subtitle_basename") or job.get("title") or mp3.stem)
        if recovering and job.get("output_path"):
            out = Path(job["output_path"])
        else:
            out = reserve_unique_path(mp3.parent / f"{basename} - subtitles v{revision}.mp4")
            out.unlink()
        part = video_part_path(out)
        set_job(job_id, status="running", stage="video", phase="render", progress=0,
                encoder_pid=None, message="re-rendering edited subtitles — 0%",
                output_path=str(out), subtitle_effective_path=str(ass_path))
        started = time.monotonic()
        progress_state = {"percent": -1, "updated": 0.0}

        def report_progress(fraction):
            percent = max(0, min(100, int(float(fraction) * 100)))
            now = time.monotonic()
            if percent < 100 and (percent <= progress_state["percent"] or
                                  now - progress_state["updated"] < 0.75):
                return
            elapsed = max(0.0, now - started)
            eta = elapsed * (1.0 - fraction) / fraction if fraction > 0.01 else None
            remaining = ""
            if eta is not None and percent < 100:
                minutes, seconds = divmod(max(0, int(round(eta))), 60)
                remaining = (f" · about {minutes}m {seconds:02d}s remaining"
                             if minutes else f" · about {seconds}s remaining")
            progress_state.update(percent=percent, updated=now)
            set_job(job_id, phase="render", progress=percent,
                    message=f"re-rendering edited subtitles — {percent}%{remaining}")

        def register_encoder(pid):
            set_job(job_id, encoder_pid=int(pid))

        render_settings = {
            "accent": job.get("subtitle_accent") or palette_for(job.get("title") or "")[2],
            "height": int(settings.get("height") or CONFIG.get("video_height") or 1080),
            "fps": int(settings.get("fps") or CONFIG.get("video_fps") or 30),
            "vis": settings.get("visualizer") or CONFIG.get("visualizer") or "bars",
            "shimmer": bool(settings.get("shimmer", CONFIG.get("shimmer", True))),
            "interlude_mode": bool(settings.get("interlude_mode", CONFIG.get("interlude_mode", True))),
            "lyric_focus_band": bool(settings.get("lyric_focus_band", CONFIG.get("lyric_focus_band", True))),
            "focus_opacity": float(settings.get("focus_opacity") or 0.90),
            "crf": int(settings.get("crf") or CONFIG.get("video_crf") or 21),
        }
        if job.get("render_backend") == "aws":
            def release_for_cloud():
                nonlocal acquired
                if acquired:
                    VIDEO_SLOTS.release()
                    acquired = False

            render_video_aws(job_id, {
                "audio": mp3, "background": bg, "subtitles": ass_path,
                "focus_background": focus_bg,
            }, render_settings, part, journal_key="cloud_revision_execution",
                release_slot=release_for_cloud)
            if not completed_video_matches(ff, part, audio_duration(ff, mp3)):
                raise RuntimeError("downloaded AWS subtitle revision failed playback verification")
        else:
            render_lyric_video(
                ff, mp3, bg, ass_path, part,
                accent=render_settings["accent"], height=render_settings["height"],
                fps=render_settings["fps"], vis=render_settings["vis"],
                shimmer=render_settings["shimmer"],
                interlude_mode=render_settings["interlude_mode"],
                lyric_focus_band=render_settings["lyric_focus_band"],
                focus_bg_png=focus_bg, focus_opacity=render_settings["focus_opacity"],
                crf=render_settings["crf"],
                progress_callback=report_progress, process_callback=register_encoder)
        part.replace(out)
        versions = list(job.get("subtitle_versions") or [])
        versions.append({"file": str(out), "name": out.name, "revision": revision,
                         "created": time.time()})
        set_job(job_id, status="paused_video", stage="video", phase="done", progress=100,
                encoder_pid=None, video_path=str(out), output_path=str(out),
                subtitle_versions=versions, subtitle_rerendering=False,
                tracks=[{"file": str(out), "name": out.name, "video": True}],
                message=f"subtitle revision {revision} ready for approval")
    except Exception as error:
        if part:
            try:
                part.unlink()
            except OSError:
                pass
        # Preserve the prior reviewable video and editor rather than turning a
        # typo in an ASS line into a dead-end pipeline error.
        set_job(job_id, status="paused_video", stage="video", encoder_pid=None,
                subtitle_rerendering=False,
                message=f"subtitle re-render failed: {str(error)[:220]}")
        print(f"[{job_id[:8]}] SUBTITLE RERENDER ERROR: {error}")
    finally:
        if acquired:
            VIDEO_SLOTS.release()


def start_subtitle_rerender(job_id):
    job = job_snapshot(job_id)
    if not job or not job.get("pipeline") or job.get("status") != "paused_video":
        raise RuntimeError("subtitle re-rendering is available when a video is waiting for approval")
    effective_subtitle_path(job)  # verifies job-owned source paths before queuing
    JOB_CANCELS[job_id] = threading.Event()
    revision = int(job.get("subtitle_revision") or 0) + 1
    set_job(job_id, status="queued", stage="video", phase="render", progress=0,
            subtitle_revision=revision, subtitle_rerendering=True,
            cloud_revision_execution=None,
            message="queued to re-render edited subtitles")
    threading.Thread(target=run_subtitle_rerender, args=(job_id,), daemon=True).start()


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
    "display lyrics": "display_lyrics",
    "sprint": "tagline", "tagline": "tagline", "subtitle": "tagline",
    "infographic": "infographic", "screen": "infographic",
    "caption": "caption", "song caption": "caption",
    "email": "recipient", "notify": "recipient", "requester": "recipient",
    "to": "recipient", "reply": "recipient", "replyto": "recipient",
    "recipient": "recipient", "delivery": "delivery_mode",
    "slack channel id": "slack_channel_id",
    "slack id": "slack_channel_id",
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
            if k in ("lyrics", "caption"):
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


def validate_display_lyrics(lyrics, display_lyrics):
    """Keep both sheets line-addressable and limited to spelling changes."""
    if display_lyrics is None:
        return
    if not display_lyrics.strip():
        raise ValueError("Display lyrics block is empty; remove it or add the matching lines.")
    spoken = parse_authored_lyrics(lyrics)
    shown = parse_authored_lyrics(display_lyrics)
    if len(spoken) != len(shown):
        raise ValueError("Display lyrics must have the same sections and lines as lyrics.")
    for section, display_section in zip(spoken, shown):
        if (section["tag"].casefold() != display_section["tag"].casefold() or
                len(section["lines"]) != len(display_section["lines"])):
            raise ValueError("Display lyrics must have the same sections and lines as lyrics.")
        for line, display_line in zip(section["lines"], display_section["lines"]):
            sung_words = line["text"].split()
            shown_words = display_line["text"].split()
            if len(sung_words) != len(shown_words) or any(
                    _chars(sung) != _chars(shown) and
                    (_chars(sung), _chars(shown)) not in {
                        ("jayson", "json"), ("randb", "rb"),
                        ("genesis", "gnsys"), ("jot", "jwt"),
                        ("pants", "pance"), ("toeful", "toefl")}
                    for sung, shown in zip(sung_words, shown_words)):
                raise ValueError(f"Display line must keep the same sung letters: {display_line['text']!r}")


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
        return normalize_delivery_fields({
            "title": title or "Untitled",
            "style": scrub_scaffolding(sect.get("style", "")).replace("\n", ", ").strip(", ")
                     or default_style,
            "lyrics": "" if instrumental else scrub_scaffolding(sect.get("lyrics", "")),
            "display_lyrics": ("" if instrumental else scrub_scaffolding(sect["display_lyrics"]))
                              if "display_lyrics" in sect else None,
            "model": (sect.get("model") or "").strip(),
            "instrumental": instrumental,
            "negativeTags": (sect.get("negativeTags") or "").strip(),
            "tagline": (sect.get("tagline") or "").strip(),
            "infographic": scrub_scaffolding(sect.get("infographic", "")),
            "caption": sect.get("caption", ""),
            "recipient": sect.get("recipient") or "",
            "delivery_mode": sect.get("delivery_mode", "none"),
            "slack_channel_id": sect.get("slack_channel_id", ""),
            "vocalGender": "f" if gender.startswith("f") else "m" if gender.startswith("m") else "",
            "styleWeight": None,
            "weirdnessConstraint": None,
        })

    fields, lyric_lines, in_lyrics = {}, [], False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not in_lyrics:
            if re.match(r"^\s*-{3,}\s*$", line):
                in_lyrics = True
                continue
            m = re.match(r"^\s*([A-Za-z][A-Za-z ]{2,30})\s*:\s*(.*)$", line)
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

    return normalize_delivery_fields({
        "title": title or "Untitled",
        "style": fields.get("style") or default_style,
        "lyrics": "" if instrumental else lyrics,
        "display_lyrics": fields.get("display_lyrics"),
        "model": fields.get("model", "").strip(),
        "instrumental": instrumental,
        "negativeTags": fields.get("negativeTags", ""),
        "tagline": fields.get("tagline", "").strip(),
        "infographic": fields.get("infographic", "").strip(),
        "caption": fields.get("caption", ""),
        "recipient": fields.get("recipient", ""),
        "delivery_mode": fields.get("delivery_mode", "none"),
        "slack_channel_id": fields.get("slack_channel_id", ""),
        "vocalGender": gender,
        "styleWeight": None,
        "weirdnessConstraint": None,
    })


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
                try:
                    validate_display_lyrics(item["form"].get("lyrics", ""),
                                            item["form"].get("display_lyrics"))
                    if CONFIG.get("auto_generate") and sender_allowed(item["from"]):
                        start_job(item["form"], source=f"auto: {item['from'][:40]}")
                        auto += 1
                        continue
                except ValueError as error:
                    item["validation_error"] = str(error)
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
MAX_IMAGE_UPLOAD_BYTES = 25 * 1024 * 1024


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

    def _upload_image(self):
        """Receive one browser FormData image without widening JSON endpoints."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "invalid Content-Length"}, 400)
        if n <= 0:
            return self._json({"error": "choose an image to upload"}, 400)
        if n > MAX_IMAGE_UPLOAD_BYTES:
            return self._json({"error": "image upload is limited to 25 MB"}, 413)
        content_type = self.headers.get("Content-Type") or ""
        if not content_type.lower().startswith("multipart/form-data;"):
            return self._json({"error": "expected a multipart image upload"}, 400)
        try:
            raw = self.rfile.read(n)
            message = email.parser.BytesParser().parsebytes(
                b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + raw)
            job_id = ""
            fields = None
            file_name, image_data = "", None
            for part in message.walk():
                if part.get_content_disposition() != "form-data":
                    continue
                name = part.get_param("name", header="content-disposition")
                payload = part.get_payload(decode=True) or b""
                if name == "image" and part.get_filename() and image_data is None:
                    file_name, image_data = part.get_filename(), payload
                elif name == "job":
                    job_id = payload.decode("utf-8", "replace").strip()
                elif name == "fields":
                    candidate = json.loads(payload.decode("utf-8", "replace"))
                    if isinstance(candidate, dict):
                        fields = candidate
            if not job_id or image_data is None:
                raise BadRequest("job and image are required")
            # Save any open song-detail edits first, matching Generate Image.
            pipeline_action(job_id, "save_fields", fields)
            add_uploaded_image(job_id, file_name, image_data)
            return self._json({"ok": True})
        except BadRequest as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            return self._json({"error": str(e) or "could not upload image"}, 400)

    # ---- routes ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")

        if u.path == "/api/aws/setup":
            return self._json(aws_setup_snapshot())

        if u.path == "/api/aws/status":
            return self._json({"state": aws_signin_status()})

        if u.path == "/api/subtitles":
            job_id = (q.get("job") or [""])[0]
            job = job_snapshot(job_id)
            if not job or not job.get("pipeline") or job.get("status") != "paused_video":
                return self._json({"error": "subtitles can be edited while a video is waiting for approval"}, 404)
            try:
                if not job.get("subtitle_generated_path"):
                    recover_subtitle_review_metadata(job_id)
                    job = job_snapshot(job_id)
                generated, override = subtitle_paths(job)
                text, sidecar, edited = subtitle_timing_text(job)
                return self._json({"text": text, "format": "timing-tsv",
                                   "revision": job.get("subtitle_revision") or 0,
                                   "edited": edited, "generated_name": generated.name,
                                   "effective_name": (override if edited else generated).name,
                                   "timing_name": sidecar.name})
            except Exception as error:
                return self._json({"error": str(error)}, 400)

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
                "slack_token_set": bool((CONFIG.get("slack_bot_token") or "").strip()),
                "auto_video": CONFIG.get("auto_video"),
                "render_backend": CONFIG.get("render_backend", "local"),
                "aws_ready": aws_ready(),
                "aws_profile": CONFIG.get("aws_profile") or "",
                "aws_region": CONFIG.get("aws_region") or "",
                "aws_render_size": CONFIG.get("aws_render_size", "large"),
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
                "ffmpeg": find_ffmpeg() or "",
                "ffmpeg_missing": missing_filters(find_ffmpeg()) if find_ffmpeg() else [],
            })

        if u.path == "/api/jobs":
            with JOBS_LOCK:
                review_ids = [j["id"] for j in JOBS.values()
                              if j.get("pipeline") and j.get("status") == "paused_video" and
                              not j.get("subtitle_generated_path")]
            for job_id in review_ids:
                recover_subtitle_review_metadata(job_id)
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
            if u.path == "/api/job/upload-image":
                return self._upload_image()
            body = self._body()
        except RequestTooLarge:
            self.close_connection = True
            return self._json({"error": "request body is too large"}, 413)
        except BadRequest as e:
            return self._json({"error": str(e)}, 400)

        if u.path == "/api/aws/setup":
            if not isinstance(body, dict):
                return self._json({"error": "expected setup details"}, 400)
            origin = self.headers.get("Origin")
            if (self.headers.get("X-Suno-Setup") != "1" or
                    (origin and origin not in (f"http://{HOST}:{PORT}",
                                               f"http://localhost:{PORT}"))):
                return self._json({"error": "Open AWS setup from this app"}, 403)
            try:
                start_aws_setup(str(body.get("action") or ""),
                                str(body.get("profile") or "").strip(),
                                str(body.get("region") or "us-east-1").strip())
                return self._json({"ok": True})
            except ValueError as error:
                return self._json({"error": str(error)}, 400)

        if u.path == "/api/config":
            if body.get("render_backend") in ("local", "aws"):
                if body["render_backend"] == "aws" and not aws_ready():
                    return self._json({"error": "Run AWS setup before choosing AWS rendering."}, 400)
                CONFIG["render_backend"] = body["render_backend"]
            if body.get("aws_render_size") in ("economy", "balanced", "large"):
                CONFIG["aws_render_size"] = body["aws_render_size"]
            for k in ("provider", "output_dir", "video_dir", "staging_dir", "rejects_dir", "kie_key", "sunoapi_key", "atlascloud_key",
                      "openai_key", "openai_image_model", "todoist_token", "todoist_project",
                      "gmail_user", "gmail_app_password", "gmail_label", "slack_bot_token",
                      "default_style", "allowed_senders"):
                if k in body and body[k] is not None:
                    v = body[k]
                    # blank secret submission = leave the stored one alone
                    if (k.endswith("_key") or k.endswith("password") or
                            k.endswith("_token")) and not str(v).strip():
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
                      "art_title", "copy_path", "shimmer",
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
                item = INBOX.get(body.get("id"))
            if not item:
                return self._json({"error": "that request is no longer pending"}, 404)
            form = dict(item["form"])
            if body.get("form"):                      # edited in the UI before approving
                form.update({k: v for k, v in body["form"].items() if v is not None})
            try:
                start_job(form, source=f"email: {item['from'][:40]}")
            except ValueError as error:
                return self._json({"error": str(error)}, 400)
            with INBOX_LOCK:
                INBOX.pop(body.get("id"), None)
                _save_inbox_locked()
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

        if u.path == "/api/subtitles/save":
            job = job_snapshot(body.get("job") or "")
            text = body.get("text")
            if not job or not job.get("pipeline") or job.get("status") != "paused_video":
                return self._json({"error": "subtitles can be saved while a video is waiting for approval"}, 400)
            try:
                word_count = save_subtitle_timing_text(job, text)
                _, override = subtitle_paths(job)
                set_job(job["id"], subtitle_effective_path=str(override),
                        message=f"saved manual word timing ({word_count} words)")
                return self._json({"ok": True, "words": word_count})
            except Exception as error:
                return self._json({"error": str(error)}, 400)

        if u.path == "/api/subtitles/reset":
            job = job_snapshot(body.get("job") or "")
            if not job or not job.get("pipeline") or job.get("status") != "paused_video":
                return self._json({"error": "subtitles can be reset while a video is waiting for approval"}, 400)
            try:
                generated, override = subtitle_paths(job)
                if override.exists():
                    override.unlink()
                subtitle_timing_path(generated).unlink(missing_ok=True)
                set_job(job["id"], subtitle_effective_path=str(generated),
                        message="restored automatic subtitle timing")
                return self._json({"ok": True})
            except Exception as error:
                return self._json({"error": str(error)}, 400)

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
                return self._json({"error": "FFmpeg is missing. See SETUP.md to install and check it."}, 400)
            return self._json({"ok": True, "id": start_video_job(track)})

        if u.path == "/api/inbox/dismiss":
            with INBOX_LOCK:
                INBOX.pop(body.get("id"), None)
                _save_inbox_locked()
            return self._json({"ok": True})

        if u.path == "/api/inbox/approve_all":
            with INBOX_LOCK:
                items = list(INBOX.values())
            count, errors = 0, []
            for item in items:
                try:
                    start_job(item["form"], source=f"email: {item['from'][:40]}")
                except ValueError as error:
                    errors.append(f"{item['form'].get('title', 'Untitled')}: {error}")
                    continue
                count += 1
                with INBOX_LOCK:
                    INBOX.pop(item["id"], None)
                    _save_inbox_locked()
            return self._json({"ok": not errors, "count": count, "errors": errors})

        if u.path == "/api/generate":
            form = {
                "title": (body.get("title") or "").strip(),
                "style": (body.get("style") or "").strip(),
                "lyrics": body.get("lyrics") or "",
                "caption": body.get("caption") or "",
                "model": body.get("model") or "",
                "instrumental": bool(body.get("instrumental")),
                "negativeTags": (body.get("negativeTags") or "").strip(),
                "tagline": (body.get("tagline") or "").strip(),
                "delivery_mode": body.get("delivery_mode", "none"),
                "recipient": body.get("recipient") or "",
                "slack_channel_id": body.get("slack_channel_id") or "",
                "vocalGender": body.get("vocalGender") or "",
                "styleWeight": body.get("styleWeight"),
                "weirdnessConstraint": body.get("weirdnessConstraint"),
            }
            if not form["lyrics"].strip() and not form["style"] and not form["instrumental"]:
                return self._json({"error": "Give me some lyrics or at least a style."}, 400)
            return self._json({"ok": True, "id": start_job(form, source="manual")})

        if u.path in ("/api/bug/preview", "/api/bug/submit"):
            import bug_reports
            stage = str(body.get("stage") or "other")
            summary = str(body.get("error_summary") or "Unexpected failure")
            if summary not in bug_reports.SAFE_SUMMARIES:
                return self._json({"error": "Choose a listed error summary."}, 400)
            preview = bug_reports.report_preview(stage, summary, APP_VERSION)
            if u.path.endswith("preview"):
                return self._json({"preview": preview})
            try:
                reference = bug_reports.send_report(preview)
            except Exception as error:
                return self._json({"error": f"Bug report could not be sent: {error}"}, 502)
            return self._json({"ok": True, "reference": reference})

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
#manualcard{margin-top:0}
#manualcard>summary{font-size:18px;color:var(--ink);text-transform:none;letter-spacing:0}
label{display:block;font-size:12px;font-weight:600;color:var(--dim);
  text-transform:uppercase;letter-spacing:.06em;margin:14px 0 6px}
label:first-child{margin-top:0}
input[type=text],input[type=password],textarea,select{width:100%;background:var(--panel2);color:var(--ink);
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
.image-action{position:relative;display:inline-flex}
.image-action-main{border-radius:7px 0 0 7px}
.image-action-caret{border-left:1px solid rgba(255,255,255,.3);border-radius:0 7px 7px 0;padding-left:9px;padding-right:9px}
.image-action-menu{position:absolute;left:0;top:calc(100% + 5px);z-index:2;
  min-width:142px;padding:5px;background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 10px 24px rgba(0,0,0,.3)}
.image-action-menu button{width:100%;text-align:left}
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
.progress.indeterminate>span{width:28%;animation:render-active 1.5s ease-in-out infinite}
@keyframes render-active{from{transform:translateX(-100%)}to{transform:translateX(360%)}}
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
#dlg{max-width:680px;max-height:85vh;overflow:auto}
#awsdlg{max-width:680px;max-height:85vh;overflow:auto}
dialog::backdrop{background:rgba(0,0,0,.6)}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.vtag{font-size:11px;font-weight:600;color:var(--dim);background:var(--panel2);
  border:1px solid var(--line);border-radius:20px;padding:2px 8px;margin-left:8px;
  vertical-align:2px;letter-spacing:.03em}
.render-status{white-space:nowrap}
.render-status[data-state="ready"]{color:var(--accent2);border-color:var(--accent2)}
.render-status[data-state="signin"],.render-status[data-state="wrong_account"]{color:var(--warn);border-color:var(--warn)}
</style></head><body>

<header>
  <div class="logo"></div>
  <h1>Suno Studio <span id="ver" class="vtag"></span></h1>
  <button id="render_status" class="ghost small render-status" data-state="checking" onclick="openRenderStatus()" title="Check rendering status">Rendering: checking…</button>
  <div class="spacer"></div>
  <button class="ghost small" onclick="reveal('')">Open delivery folder</button>
  <button class="ghost small" onclick="openGuide()">Getting started</button>
  <button class="ghost small" onclick="openSettings()">Settings</button>
  <button class="ghost small" onclick="openBugReport()">Report a bug</button>
  <button class="ghost small" onclick="quitApp()">Quit</button>
</header>

<div class="wrap">
  <div>
    <div id="warn"></div>
    <div class="card" id="setupcard" style="display:none;margin-bottom:20px">
      <h2 style="font-size:18px;margin:0 0 3px">Make your first lyric video</h2>
      <div class="hint" id="setupstatus"></div>
      <div class="btns" style="margin-top:12px">
        <button class="small" onclick="openGuide()">Show setup steps and costs</button>
        <button class="ghost small" onclick="openSettings()">Open Settings</button>
      </div>
    </div>
    <div class="banner" id="alertbar" style="display:none"></div>
    <div class="watchbar" id="watchbar" style="display:none"></div>

    <details class="card" id="manualcard" style="margin-bottom:20px">
      <summary>Create a Song</summary>
      <div class="hint">Enter a title, style, and lyrics to start a new song.</div>
      <label for="title">Title</label>
      <input type="text" id="title" placeholder="A title for your song">
      <label for="style">Style</label>
      <input type="text" id="style" placeholder="indie folk, acoustic guitar, warm">
      <label for="lyrics">Lyrics</label>
      <textarea id="lyrics" placeholder="[Verse 1]\nYour lyrics here…"></textarea>
      <label for="manual_caption">Song Caption (optional)</label>
      <input type="text" id="manual_caption" maxlength="239" placeholder="Message to accompany the finished video">
      <div class="row">
        <div>
          <label for="manual_delivery_mode">Delivery</label>
          <select id="manual_delivery_mode" onchange="syncManualDeliveryFields()">
            <option value="none" selected>None</option>
            <option value="slack">Slack</option>
            <option value="email">Email</option>
          </select>
          <div class="hint">Sending starts only after video approval. None keeps the video on this computer.</div>
        </div>
        <div id="manual_delivery_target"></div>
      </div>
      <div class="actions">
        <button id="go" onclick="generate()">Generate Song</button>
        <span class="hint" style="margin:0">Songs and videos remain available in Finished Jobs.</span>
      </div>
    </details>

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

<dialog id="guidedlg" style="max-width:720px;max-height:85vh;overflow:auto">
  <h2 style="margin:0 0 4px">Getting started</h2>
  <p style="margin:0">Start with a song provider and local video tools. Add the other services only when you want their features.</p>
  <ol>
    <li><b>Install Python 3.9+ and FFmpeg.</b> Both are free. FFmpeg needs subtitles and drawtext support. <span id="guide_video_status"></span></li>
    <li><b>Choose a song provider in Settings and add its API key.</b> This is the only paid service needed for a song. <a href="https://kie.ai/" target="_blank" rel="noreferrer">kie.ai</a> (the default) and <a href="https://sunoapi.org/" target="_blank" rel="noreferrer">sunoapi.org</a> support your exact lyrics; <a href="https://www.atlascloud.ai/" target="_blank" rel="noreferrer">Atlas Cloud</a> may paraphrase them. Your title, style, and lyrics go to the chosen provider. <span id="guide_song_status"></span></li>
    <li><b>Use Create a Song.</b> Enter a title, style, and lyrics; leave Delivery at None. Review the result and keep the MP4 on this computer.</li>
  </ol>
  <h3>Optional services</h3>
  <ul>
    <li><b>OpenAI:</b> creates AI artwork instead of the free gradient. The lyric focus band may make a second image request. Hosted lyric repair also uses OpenAI when selected.</li>
    <li><b>Gmail:</b> reads requests from a chosen label or sends an approved video's link. Email links need AWS. A Google app password is needed; ordinary Gmail use has no app fee.</li>
    <li><b>Slack:</b> sends an approved MP4 to a channel. Local sending needs a bot token; cloud sending uses a token stored during AWS setup. Your Slack plan may have limits.</li>
    <li><b>AWS:</b> runs parallel video encodes and hosts private three-day email links. It is pay per use, but stored images and logs can have small ongoing charges. Local rendering is free.</li>
    <li><b>Todoist:</b> optional low-credit alerts. <b>Sentry:</b> optional bug reports to the maintainer; you need no account for either to make a video.</li>
  </ul>
  <h3>What might one video cost?</h3>
  <p>With local rendering and gradient artwork, you pay only your song provider. Its per-request price depends on your provider, model, and plan. Check its dashboard before generating.</p>
  <p>Optional AI artwork adds the provider's price for one image, or two if the focus-band edit runs. As a planning allowance, budget <b>cents to tens of cents per image</b> and check <a href="https://developers.openai.com/api/docs/pricing" target="_blank" rel="noreferrer">current OpenAI pricing</a>.</p>
  <p>Optional AWS rendering adds about <b>$0.01–$0.03 in render compute</b> for a three-minute sample, based on measured tasks in us-east-1. Storage, logs, transfers, setup builds, and delivery add to that. A failed task or video retry can be charged again. <a href="https://aws.amazon.com/fargate/pricing/" target="_blank" rel="noreferrer">Check regional AWS rates</a>.</p>
  <p class="hint">Example only: if a song request costs $0.25, each image costs $0.08, and an AWS render costs $0.02, the total is about $0.27 with gradient art or $0.43 with two image requests, before small AWS extras. These are example inputs, not quoted provider prices.</p>
  <p class="hint">For AWS setup commands, permissions, and cleanup, see <a href="https://github.com/mbelinkie/sunostudio/blob/main/SETUP.md" target="_blank" rel="noreferrer">the full setup guide</a>.</p>
  <div class="actions"><button onclick="guidedlg.close();openSettings()">Open Settings</button>
    <button class="ghost" onclick="guidedlg.close()">Close</button></div>
</dialog>

<dialog id="bugdlg">
  <h3 style="margin:0 0 4px">Report a bug</h3>
  <div class="hint">Choose what failed. Preview the exact details before sending them to Sentry.</div>
  <label for="bug_stage">Stage</label>
  <select id="bug_stage" onchange="previewBugReport()">
    <option value="setup">Setup</option><option value="intake">Intake</option>
    <option value="song">Song</option><option value="artwork">Artwork</option>
    <option value="subtitles">Subtitles</option><option value="video">Video</option>
    <option value="delivery">Delivery</option><option value="interface">Interface</option>
    <option value="other">Other</option>
  </select>
  <label for="bug_summary">Error summary</label>
  <select id="bug_summary" onchange="previewBugReport()">
    <option>Unexpected failure</option><option>Rate limit or quota reached</option>
    <option>Access denied</option><option>Operation timed out</option>
    <option>Network connection failed</option><option>Video encoding failed</option>
    <option>File transfer failed</option><option>Required input was invalid or missing</option>
  </select>
  <label for="bug_preview">Only these details will be sent</label>
  <pre id="bug_preview" class="mono" style="white-space:pre-wrap"></pre>
  <div class="hint">Lyrics, media, paths, account details, and credentials are excluded. Sending is optional.</div>
  <div class="actions"><button id="bug_send" onclick="sendBugReport()">Send report</button>
    <button class="ghost" onclick="bugdlg.close()">Cancel</button></div>
  <div id="bug_result" class="hint"></div>
</dialog>

<dialog id="dlg">
  <h3 style="margin:0 0 4px">Settings</h3>
  <div class="hint">Changes are saved locally when you choose Save.</div>
  <details class="gate-panel"><summary>Approval steps</summary>
    <div class="hint">Turn a gate on to stop the pipeline at that stage until you approve it. Turn all three off for hands-off processing.</div>
    <label class="check"><input type="checkbox" id="s_gate_song"> Require approval after song generation</label>
    <div class="hint">Choose or edit a generated song before artwork starts.</div>
    <label class="check"><input type="checkbox" id="s_gate_image"> Require approval after image generation</label>
    <div class="hint">Choose or regenerate artwork before lyric-video rendering starts.</div>
    <label class="check"><input type="checkbox" id="s_gate_video"> Require approval after video rendering</label>
    <div class="hint">Inspect the finished video before publishing it to Final.</div>
  </details>
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
  </details>
  </div>

  <details class="settings-section"><summary>Email intake and delivery</summary>
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

  <label>Check every (sec)</label><input type="text" id="s_ws" placeholder="60">

  <label class="check" style="margin-top:14px"><input type="checkbox" id="s_auto"> Skip approval and generate automatically</label>
  <div class="hint">Only fires for senders listed below. Leave the list empty and nothing auto-fires.</div>
  <label>Trusted senders</label>
  <input type="text" id="s_as" placeholder="rovo@yourcompany.com, you@example.com">

  <div class="actions">
    <button class="ghost" onclick="testGmail()">Test Gmail connection</button>
    <span class="hint" style="margin:0" id="s_test"></span>
  </div>
  </details>

  <details class="settings-section"><summary>Slack delivery</summary>
  <label>Slack bot token</label>
  <input type="password" id="s_slack_token" placeholder="xoxb-… (leave blank to keep current)">
  <div class="hint" id="s_slack_state"></div>
  <div class="hint">Local Slack delivery uploads the approved MP4 to the selected channel. The bot needs file upload and channel posting permissions and must belong to the channel.</div>
  </details>

  <details class="settings-section"><summary>Credit alerts</summary>
  <label class="check" style="margin-top:14px"><input type="checkbox" id="s_al"> Warn me before the accounts run dry</label>
  <div class="hint">Checks kie.ai hourly. OpenAI publishes no balance, so that one is
  caught the first time it reports an exhausted quota.</div>
  <div class="row">
    <div><label>Warn under (kie.ai credits)</label><input type="text" id="s_alk" placeholder="100"></div>
    <div><label>Todoist API token</label><input type="password" id="s_tdt" placeholder="optional"></div>
  </div>
  </details>

  <div class="settings-section"><h4>Lyric video</h4>
  <label>Video rendering</label>
  <select id="s_backend"><option value="local">On this computer</option><option value="aws">AWS parallel jobs</option></select>
  <div class="hint" id="s_backend_state"></div>
  <details id="aws_settings"><summary>AWS setup and task size</summary>
    <p class="hint">AWS runs paid parallel video jobs. Profiles and credentials stay on this computer; each person connects their own AWS account. Check the account and region before creating resources there. That checked account pays for the jobs. You can change task size later without rebuilding the worker.</p>
    <div class="row"><div><label for="s_aws_profile">AWS profile</label><input type="text" id="s_aws_profile" list="aws_profiles" oninput="awsAccountChanged()" placeholder="default or named SSO profile"><datalist id="aws_profiles"></datalist></div>
      <div><label for="s_aws_region">AWS region</label><input type="text" id="s_aws_region" oninput="awsAccountChanged()" placeholder="us-east-1"></div></div>
    <div class="hint">Profile sign-in requires <a href="https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" target="_blank" rel="noreferrer">AWS CLI v2</a>. If you have no profile yet, follow the <a href="https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html" target="_blank" rel="noreferrer">AWS sign-in setup</a> once. No access keys are stored in Suno Studio.</div>
    <div class="btns" style="margin-top:12px"><button type="button" class="ghost small" onclick="awsStep('signin')">Sign in</button>
      <button type="button" class="ghost small" onclick="awsStep('check')">Check account</button>
      <button type="button" class="small" id="s_aws_setup" onclick="awsStep('setup')" disabled>Create AWS resources</button></div>
    <button type="button" class="ghost small" onclick="openAwsProgress()">View setup status</button>
    <label for="s_aws_size">Render task size</label>
    <select id="s_aws_size"><option value="economy">2 vCPU · 4 GB</option><option value="balanced">4 vCPU · 8 GB</option><option value="large">8 vCPU · 16 GB (fastest measured)</option></select>
    <div class="hint">Applies to new AWS videos. Larger tasks cost more per minute and use more regional quota. Measured three-minute samples cost about $0.01–$0.03 in render compute, plus storage and transfer.</div>
  </details>
  <div class="row">
    <div><label style="margin-top:0">Resolution</label>
      <select id="s_vh"><option value="1080">1080p</option><option value="720">720p (faster)</option></select></div>
    <div><label style="margin-top:0">Background</label>
      <select id="s_bg"><option value="gradient">Generated gradient</option><option value="ai">AI artwork</option></select></div>
    <div><label style="margin-top:0">Visualizer</label>
      <select id="s_vis"><option value="bars">Spectrum bars</option><option value="wave">Waveform</option><option value="off">None</option></select></div>
  </div>
  <details><summary>Subtitle timing and output folder</summary><label>Lyric timing</label>
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
  </details>
  <label class="check" style="margin-top:12px"><input type="checkbox" id="s_av"> Render a lyric video automatically after each song</label>
  <div class="hint" id="s_ffstate"></div>
  </div>

  <details class="settings-section"><summary>Artwork and video effects</summary>
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
  <details>
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
  </details>

  <div class="actions">
    <button onclick="saveSettings()">Save</button>
    <button class="ghost" onclick="dlg.close()">Cancel</button>
    <div class="spacer"></div>
  </div>
  <div class="hint mono" id="s_path"></div>
</dialog>

<dialog id="awsdlg">
  <h3 style="margin:0 0 4px">AWS setup</h3>
  <div class="hint" id="s_aws_account"></div>
  <pre id="s_aws_log" class="mono" aria-live="polite" style="white-space:pre-wrap;max-height:45vh;overflow:auto"></pre>
  <div class="hint">Closing this window hides the output; any setup already started continues.</div>
  <div class="actions"><button class="ghost" onclick="awsdlg.close()">Close</button></div>
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
  const notices=[];
  if(CFG.has_key && !CFG.exact_lyrics) notices.push(p.label+" may paraphrase submitted lyrics. Choose a provider with exact-lyrics support if needed.");
  $('warn').innerHTML = notices.map(message=>`<div class="banner">${esc(message)}</div>`).join('');
  const needsVideo = !CFG.ffmpeg || (CFG.ffmpeg_missing||[]).length;
  $('setupcard').style.display = (!CFG.has_key || needsVideo) ? 'block' : 'none';
  $('setupstatus').textContent = !CFG.has_key && needsVideo ? 'Add a song-provider key and install compatible FFmpeg to begin.'
    : !CFG.has_key ? 'Add a song-provider key to begin.' : 'Install compatible FFmpeg to make videos.';
  $('guide_song_status').textContent = CFG.has_key ? 'Ready.' : 'Key needed.';
  $('guide_video_status').textContent = needsVideo ? 'FFmpeg needs attention.' : 'FFmpeg ready.';
  refreshRenderStatus();
}

function openGuide(){ $('guidedlg').showModal(); }

async function refreshRenderStatus(){
  const badge=$('render_status');
  if(!badge) return;
  try{
    const response=await fetch('/api/aws/status');
    if(!response.ok) throw new Error('status unavailable');
    const {state}=await response.json();
    const labels={local:'Rendering: this computer',setup:'Cloud: setup needed',
      ready:'Cloud: ready',signin:'Cloud: sign in needed',
      wrong_account:'Cloud: wrong account'};
    badge.dataset.state=state;
    badge.textContent=labels[state]||'Cloud: check Settings';
  }catch(_error){
    badge.dataset.state='signin';
    badge.textContent='Cloud: status unavailable';
  }
}

function openRenderStatus(){
  openSettings();
  if($('render_status').dataset.state==='signin') awsStep('signin');
}

async function generate(){
  const deliveryMode = $('manual_delivery_mode').value || 'none';
  const body = {
    title: $('title').value, style: $('style').value, lyrics: $('lyrics').value,
    caption: $('manual_caption').value,
    delivery_mode: deliveryMode,
    recipient: deliveryMode==='email' ? $('manual_recipient').value : '',
    slack_channel_id: deliveryMode==='slack' ? $('manual_slack_channel').value : '',
  };
  $('go').disabled = true;
  try {
    const r = await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j = await r.json();
    if(j.error){ alert(j.error); return; }
    refresh();
  } catch(e) { alert('Could not start song generation: '+e.message); }
  finally { $('go').disabled = false; }
}

function syncManualDeliveryFields(){
  const mode=$('manual_delivery_mode').value;
  const target=$('manual_delivery_target');
  if(mode==='email') target.innerHTML='<label for="manual_recipient">Recipient</label><input type="text" id="manual_recipient" placeholder="name@example.com">';
  else if(mode==='slack') target.innerHTML='<label for="manual_slack_channel">Slack Channel ID</label><input type="text" id="manual_slack_channel" placeholder="C0123456789">';
  else target.innerHTML='<label>Destination</label><div class="hint" style="padding:10px 0">No delivery destination needed.</div>';
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
    const display = f.display_lyrics ? f.display_lyrics.split('\n').slice(0,8).join('\n') : '';
    const bits = [f.style||'no style', f.model||'default model'];
    if(f.tagline) bits.unshift(f.tagline);
    if(f.delivery_mode==='email') bits.push('Email → '+(f.recipient||'recipient needed'));
    else if(f.delivery_mode==='slack') bits.push('Slack → '+(f.slack_channel_id||'channel needed'));
    if(f.instrumental) bits.push('instrumental');
    return `<div class="ib">
      <h3>${esc(f.title||'Untitled')}</h3>
      <div class="src">${esc(it.from||'')}${it.received?' · '+esc(it.received):''} · ${esc(bits.join(' · '))}</div>
      <pre>${esc(preview)}</pre>${display?`<div class="src">Onscreen lyrics</div><pre>${esc(display)}</pre>`:''}
      ${it.validation_error?`<div class="banner">${esc(it.validation_error)}</div>`:''}
      ${f.delivery_error?`<div class="banner">${esc(f.delivery_error)}</div>`:''}
      <div class="btns">
        <button class="small" onclick="approve('${it.id}')">Generate Song</button>
        <button class="ghost small" onclick="dismiss('${it.id}')">Dismiss</button>
      </div></div>`;
  }).join('');
}

async function approve(id){
  const result=await (await fetch('/api/inbox/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})})).json();
  if(result.error) alert(result.error);
  refresh();
}
async function approveAll(){
  if(INBOX.length>1 && !confirm(`Generate all ${INBOX.length} requests? Each one costs credits.`)) return;
  const result=await (await fetch('/api/inbox/approve_all',{method:'POST'})).json();
  if(result.errors?.length) alert(result.errors.join('\n'));
  refresh();
}
async function dismiss(id){
  await fetch('/api/inbox/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  refresh();
}
function edit(id){
  const it = INBOX.find(x=>x.id===id); if(!it) return;
  const f = it.form || {};
  $('title').value = f.title||''; $('style').value = f.style||'';
  $('lyrics').value = f.lyrics||'';
  $('manual_delivery_mode').value = f.delivery_mode||'none';
  syncManualDeliveryFields();
  if(f.delivery_mode==='email' && $('manual_recipient')) $('manual_recipient').value=f.recipient||'';
  if(f.delivery_mode==='slack' && $('manual_slack_channel')) $('manual_slack_channel').value=f.slack_channel_id||'';
  dismiss(id);
  window.scrollTo({top:0,behavior:'smooth'});
  $('title').focus();
}

// Repainting the queue would tear down any <audio> that's mid-playback, so
// only touch the DOM when the markup actually changed AND nothing is playing.
let lastActiveHtml = '', lastFinishedHtml = '', pendingJobsHtml = null;
// Job markup refreshes every few seconds. Keep each native <details> state
// separately so a refresh cannot reopen a section the user just collapsed.
const JOB_SECTION_OPEN = new Map();

function snapshotJobSectionState(){
  document.querySelectorAll('details[data-job-section]').forEach(el=>{
    JOB_SECTION_OPEN.set(el.dataset.jobSection, el.open);
  });
}

function sectionAttrs(job, section, initiallyOpen){
  const key=`${job}:${section}`;
  const open=JOB_SECTION_OPEN.has(key) ? JOB_SECTION_OPEN.get(key) : initiallyOpen;
  return ` data-job-section="${key}"${open?' open':''}`;
}

function mediaBusy(el){
  return !!el.querySelector('[data-subtitle-editor][data-active="true"]') ||
    !!el.querySelector('details[data-job-section$=":song_editor"][open]') ||
    [...el.querySelectorAll('audio,video')].some(m => !m.paused && !m.ended && m.currentTime > 0);
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
function toggleImageMenu(job, button){
  const menu=$('image_menu_'+job), opening=menu.hidden;
  document.querySelectorAll('.image-action-menu').forEach(m=>m.hidden=true);
  menu.hidden=!opening;
  button.setAttribute('aria-expanded',String(opening));
}
async function uploadPipelineImage(job, input){
  const image=input.files && input.files[0];
  if(!image) return;
  const data=new FormData();
  data.append('job',job);
  data.append('fields',JSON.stringify(gateFields(job)));
  data.append('image',image);
  input.disabled=true;
  try {
    const response=await fetch('/api/job/upload-image',{method:'POST',body:data});
    const result=await response.json();
    if(result.error) alert(result.error);
  } catch(e) {
    alert('Could not upload image: '+e.message);
  } finally {
    input.value=''; input.disabled=false; refresh();
  }
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
  const fields=gateFields(job), mode=$(fieldId(job,'delivery_mode'))?.value||'none';
  fields.delivery_mode=mode;
  fields.caption=$(fieldId(job,'caption'))?.value||'';
  fields.recipient=mode==='email'?($(fieldId(job,'recipient'))?.value||''):'';
  fields.slack_channel_id=mode==='slack'?($(fieldId(job,'slack_channel_id'))?.value||''):'';
  return fields;
}
function deliveryModeChanged(job){
  const mode=$(fieldId(job,'delivery_mode'))?.value||'none';
  const emailBox=$(fieldId(job,'email_target')), slackBox=$(fieldId(job,'slack_target'));
  if(emailBox) emailBox.hidden=mode!=='email';
  if(slackBox) slackBox.hidden=mode!=='slack';
}
function deliveryEditor(j){
  const id=j.id, f=j.current_fields||{}, mode=f.delivery_mode||'none';
  const invalid=!['none','email','slack'].includes(mode);
  const options=`${invalid?`<option value="${esc(mode)}" selected>Invalid choice: ${esc(mode)}</option>`:''}<option value="none" ${mode==='none'?'selected':''}>None</option><option value="slack" ${mode==='slack'?'selected':''}>Slack</option><option value="email" ${mode==='email'?'selected':''}>Email</option>`;
  return `<div class="delivery-review"><label for="${fieldId(id,'caption')}">Song Caption</label><input type="text" id="${fieldId(id,'caption')}" value="${esc(f.caption||'')}" maxlength="239" placeholder="Optional message to accompany the video"><label>Delivery</label><select id="${fieldId(id,'delivery_mode')}" onchange="deliveryModeChanged('${id}')">${options}</select><div id="${fieldId(id,'email_target')}" ${mode==='email'?'':'hidden'}><label>Recipient</label><input type="text" id="${fieldId(id,'recipient')}" value="${esc(f.recipient||'')}" placeholder="name@example.com"></div><div id="${fieldId(id,'slack_target')}" ${mode==='slack'?'':'hidden'}><label>Slack Channel ID</label><input type="text" id="${fieldId(id,'slack_channel_id')}" value="${esc(f.slack_channel_id||'')}" placeholder="C0123456789"></div></div>`;
}
async function retryDelivery(job, status, mode){
  const fields=deliveryFields(job);
  if(status==='needs_review' && fields.delivery_mode!=='none'){
    const warning=mode==='slack'
      ?'Slack may already have received this file. Retrying could post a duplicate.'
      :'The email may already have been sent. Retrying could send it twice.';
    if(!confirm(warning+' Retry anyway?')) return;
    fields.confirm_uncertain_delivery=true;
  }
  await pipelineAction(job,'retry_delivery',null,fields);
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
  return `<details${sectionAttrs(id,'song_editor',false)} style="margin-top:10px"><summary>Edit Song Details</summary><div class="row"><div><label>Title</label><input id="${fieldId(id,'title')}" value="${esc(f.title||'')}"></div><div><label>Tagline</label><input id="${fieldId(id,'tagline')}" value="${esc(f.tagline||'')}"></div></div><label>Genre / Style</label><input id="${fieldId(id,'style')}" value="${esc(f.style||'')}"><label>Lyrics</label><textarea id="${fieldId(id,'lyrics')}" style="min-height:120px">${esc(f.lyrics||'')}</textarea><label>Infographic</label><textarea id="${fieldId(id,'infographic')}" style="min-height:80px">${esc(f.infographic||'')}</textarea><div class="btns"><button class="ghost small" onclick="saveGateFields('${id}')">Save Song Details</button><button class="ghost small" onclick="pipelineAction('${id}','revert_email')">Revert To Email Original</button></div>${j.stale_song?'<div class="hint" style="color:var(--warn)">Lyrics or genre changed: generate a new song before continuing.</div>':''}</details>`;
}
async function openSubtitleEditor(job){
  const editor=$('subtitle_editor_'+job), text=$('subtitle_text_'+job), state=$('subtitle_state_'+job);
  if(editor.dataset.active==='true'){
    editor.dataset.active='false'; editor.hidden=true; refresh(); return;
  }
  state.textContent='Converting word timings…';
  try {
    const r=await (await fetch('/api/subtitles?job='+encodeURIComponent(job))).json();
    if(r.error) throw new Error(r.error);
    text.value=r.text||'';
    editor.dataset.active='true'; editor.hidden=false;
    state.textContent=(r.edited?'Editing saved word timings':'Editing automatic word timings')+
      ` · ${r.revision||0} rendered revision(s)`;
  } catch(e) {
    state.textContent=''; alert('Could not load subtitles: '+e.message);
  }
}
async function saveSubtitleEditor(job){
  const state=$('subtitle_state_'+job), text=$('subtitle_text_'+job);
  state.textContent='Validating and saving…';
  try {
    const r=await (await fetch('/api/subtitles/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({job,text:text.value})})).json();
    if(r.error) throw new Error(r.error);
    state.textContent=`Saved ${r.words} word timings. Regenerate to preview the timing.`;
    const editor=document.getElementById('subtitle_editor_'+job);
    editor?.querySelector('[onclick^="resetSubtitleEditor"]').removeAttribute('disabled');
    return true;
  } catch(e) { state.textContent=''; alert('Could not save subtitles: '+e.message); return false; }
}
async function regenerateSubtitles(job){
  const editor=$('subtitle_editor_'+job);
  if(editor?.dataset.active==='true' && !(await saveSubtitleEditor(job))) return;
  if(editor){ editor.dataset.active='false'; editor.hidden=true; }
  await pipelineAction(job,'rerender_subtitles');
}
async function resetSubtitleEditor(job){
  if(!confirm('Discard your saved subtitle timing edits and restore the automatic baseline?')) return;
  const state=$('subtitle_state_'+job);
  try {
    const r=await (await fetch('/api/subtitles/reset',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({job})})).json();
    if(r.error) throw new Error(r.error);
    $('subtitle_editor_'+job).dataset.active='false';
    refresh();
  } catch(e) { state.textContent=''; alert('Could not reset subtitles: '+e.message); }
}
function subtitleEditor(j){
  if(j.status!=='paused_video' || !j.subtitle_generated_path) return '';
  const id=j.id, edited=j.subtitle_effective_path===j.subtitle_override_path;
  return `<details${sectionAttrs(id,'subtitles',false)}><summary>Word Timing Editor</summary><div class="hint">Edit one word at a time using absolute timestamps—no ASS <code>{\\kf…}</code> math. Keep IDs and text unchanged; set a start such as <code>00:00:26.000</code>, then regenerate to preview.</div><div class="btns" style="margin-top:10px"><button class="ghost small" onclick="openSubtitleEditor('${id}')">Edit Word Timings</button><button class="ghost small" onclick="regenerateSubtitles('${id}')">Regenerate With Timing Edits</button></div><div id="subtitle_editor_${id}" data-subtitle-editor data-active="false" hidden><label>Timing TSV ${edited?'· saved manual timing':'· automatic baseline'}</label><textarea id="subtitle_text_${id}" spellcheck="false" style="min-height:360px"></textarea><div class="btns" style="margin-top:10px"><button class="small" onclick="saveSubtitleEditor('${id}')">Save Word Timings</button><button class="ghost small" onclick="resetSubtitleEditor('${id}')" ${edited?'':'disabled'}>Reset To Automatic</button><button class="ghost small" onclick="openSubtitleEditor('${id}')">Close</button><span class="hint" id="subtitle_state_${id}" style="margin:0"></span></div></div></details>`;
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
  return `<details${sectionAttrs(j.id,'image',true)}><summary>Image Generation · ${j.image_regenerations||0} Additional Images</summary><label>Selected Image</label><select id="pi_${j.id}" ${locked?'disabled':''} onchange="selectVariant('${j.id}','image',this.value)">${opts}</select>${locked?'<div class="hint">Locked to the gallery image used by this video.</div>':''}${preview}<div class="image-variants" aria-label="Image Options">${gallery}</div><label>Image Prompt</label><textarea id="pp_${j.id}" style="min-height:150px">${esc(current.prompt||'')}</textarea></details>`;
}
function pipelineButtons(j, songTracks, videoTracks, progress){
  if(!j.pipeline) return '';
  const id = j.id;
  const songs=`<details${sectionAttrs(id,'song',true)}><summary>Song Generation</summary>${songTracks}${songPicker(j)}${songEditor(j)}</details>`;
  const images=imagePicker(j);
  const video=(j.stage==='video'||videoTracks) ? `<details${sectionAttrs(id,'video',true)}><summary>Video Generation</summary>${progress}<div class="hint" style="margin-top:8px">${esc(j.message||'')}</div>${videoTracks}</details>` : '';
  const fields=`gateFields('${id}')`;
  const newSong=`<button class="ghost small" onclick="pipelineAction('${id}','resubmit_song',null,${fields})">Generate New Song</button>`;
  const anotherImage=`<button class="ghost small" onclick="pipelineAction('${id}','regenerate_image',document.getElementById('pp_${id}')?.value,${fields})">Generate Another Image</button>`;
  const cancel=`<button class="ghost small" onclick="cancelAndRemove('${id}')">Cancel And Remove</button>`;
  const imageAction=`<div class="image-action"><button class="small image-action-main" ${j.stale_song?'disabled':''} onclick="pipelineAction('${id}','approve_song',null,${fields})">Generate Image</button><button type="button" class="small image-action-caret" ${j.stale_song?'disabled':''} aria-label="More image options" aria-expanded="false" onclick="toggleImageMenu('${id}',this)">▾</button><div id="image_menu_${id}" class="image-action-menu" hidden><button type="button" class="ghost small" onclick="document.getElementById('image_upload_${id}').click()">Upload Image</button></div><input id="image_upload_${id}" type="file" accept="image/png,image/jpeg,image/webp" hidden onchange="uploadPipelineImage('${id}',this)"></div>`;
  const running=(j.status==='queued'||j.status==='running'||j.status==='submitting');
  const stop=running?`<div class="btns" style="margin-top:10px"><button class="ghost small" onclick="pipelineAction('${id}','interrupt')">Interrupt</button>${cancel}</div>`:'';
  if(j.status==='paused_song') return songs+`<div class="btns" style="margin-top:10px">${imageAction}${newSong}${cancel}</div>`;
  if(j.status==='paused_image') return songs+images+`<div class="btns" style="margin-top:10px"><button class="small" onclick="pipelineAction('${id}','approve_image',null,${fields},{stage:'image',id:document.getElementById('pi_${id}').value})">Generate Video</button>${anotherImage}${newSong}${cancel}</div>`;
  if(j.status==='error' && j.stage==='song' && j.task_id) return songs+`<div class="btns" style="margin-top:10px"><button class="small" onclick="pipelineAction('${id}','retry_song_poll')">Retry Provider Status</button>${newSong}${cancel}</div>`;
  if(j.status==='error' && j.stage==='image') return songs+images+`<div class="btns" style="margin-top:10px">${anotherImage}${newSong}${cancel}</div>`;
  if(j.status==='paused_video') {
    return songs+images+video+subtitleEditor(j)+deliveryEditor(j)+`<div class="btns" style="margin-top:10px"><button class="small" onclick="pipelineAction('${id}','approve_video',null,deliveryFields('${id}'))">Approve Video</button><button class="ghost small" onclick="pipelineAction('${id}','back_image')">Back to Image</button>${cancel}</div>`;
  }

  if(running && j.stage==='image') return songs+images+`<div class="hint" style="margin-top:10px"><span class="spin"></span>Generating Another Image — Existing Options Remain Available.</div>`+stop;
  if(running && j.stage==='video') return songs+images+video+stop;
  if(j.status==='completed' && j.delivery_status && j.delivery_status!=='not_requested'){
    const deliveryState=j.delivery_status==='sent'?'Delivery sent':
      j.delivery_status==='needs_review'?'Delivery needs your review':
      j.delivery_status==='queued'||j.delivery_status==='sending'?'Sending delivery…':'Delivery error';
    const detail=j.delivery_error?`<div class="banner" style="margin-top:10px">${esc(j.delivery_error)}</div>`:
      (j.delivery_receipt&&j.delivery_receipt.permalink?`<div class="hint"><a href="${esc(j.delivery_receipt.permalink)}" target="_blank" rel="noreferrer">Open Slack file</a></div>`:'');
    const retry=['error','needs_review'].includes(j.delivery_status)
      ?`<div style="margin-top:8px">${deliveryEditor(j)}<button class="small" onclick="retryDelivery('${id}','${j.delivery_status}','${j.delivery_mode}')">Retry Delivery</button></div>`:'';
    return songs+images+video+`<div class="delivery-result"><b>${esc(deliveryState)}</b>${detail}${retry}</div>`;
  }
  if(j.status==='error') return songs+images+video+`<div class="btns" style="margin-top:10px">${j.stage==='video'?`<button class="small" onclick="pipelineAction('${id}','retry_video')">Retry video</button>`:''}${cancel}</div>`;
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
  snapshotJobSectionState();
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
    const rendering = run && j.stage==='video' && j.phase==='render';
    const hasProgress = rendering && j.progress!==null && j.progress!==undefined &&
      Number.isFinite(Number(j.progress));
    const percent = hasProgress ? Math.max(0,Math.min(100,Number(j.progress))) : 0;
    const renderWhere = j.render_backend==='aws' ? 'AWS cloud' : 'this computer';
    const progress = rendering ? `<div class="hint">Rendering on ${renderWhere}${hasProgress?' · '+Math.round(percent)+'%':''}</div>
      <div class="progress${hasProgress?'':' indeterminate'}" role="progressbar" aria-label="Video render progress on ${renderWhere}"
      ${hasProgress?`aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"`:''}><span${hasProgress?` style="width:${percent}%"`:''}></span></div>` : '';
    const body = j.pipeline
      ? pipelineButtons(j,songTracks,videoTracks,progress)
      : progress+songTracks+videoTracks;
    return `<details class="job"${sectionAttrs(j.id,'job',true)}>
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
const awsdlg = $('awsdlg');
const bugdlg = $('bugdlg');
function openBugReport(){ $('bug_result').textContent=''; bugdlg.showModal(); previewBugReport(); }
async function previewBugReport(){
  const body={stage:$('bug_stage').value,error_summary:$('bug_summary').value};
  const r=await (await fetch('/api/bug/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  $('bug_preview').textContent=r.preview?JSON.stringify(r.preview,null,2):(r.error||'Preview unavailable');
}
async function sendBugReport(){
  const button=$('bug_send'); button.disabled=true;
  const body={stage:$('bug_stage').value,error_summary:$('bug_summary').value};
  try{
    const r=await (await fetch('/api/bug/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    $('bug_result').textContent=r.ok?`Report sent. Reference: ${r.reference}`:(r.error||'Report failed');
  }catch(e){ $('bug_result').textContent='Report could not be sent. Check your connection.'; }
  button.disabled=false;
}
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
  $('s_slack_token').value = '';
  $('s_slack_state').textContent = CFG.slack_token_set ? 'A Slack bot token is already saved.'
    : 'Optional for local Slack delivery. Cloud delivery uses the token stored by cloud setup.';
  $('s_ds').value = CFG.default_style||'';
  $('s_ws').value = CFG.watch_seconds||60;
  $('s_cap').value = CFG.max_concurrent||2;
  $('s_stage').value = CFG.staging_dir||''; $('s_rejects').value = CFG.rejects_dir||'';
  $('s_purge').value = CFG.reject_purge_days||14;
  $('s_gate_song').checked=!!CFG.gate_song; $('s_gate_image').checked=!!CFG.gate_image;
  $('s_gate_video').checked=!!CFG.gate_video;
  $('s_auto').checked = !!CFG.auto_generate;
  $('s_as').value = CFG.allowed_senders||'';
  $('s_test').textContent = '';
  $('s_vh').value = String(CFG.video_height||1080);
  $('s_backend').value = CFG.render_backend||'local';
  $('s_aws_size').value = CFG.aws_render_size||'large';
  $('s_backend').querySelector('option[value="aws"]').disabled = !CFG.aws_ready;
  $('s_backend_state').textContent = CFG.aws_ready
    ? `AWS configured in ${CFG.aws_region}. Sign in with the saved AWS profile before rendering; cloud jobs are billed per task.`
    : 'Optional pay-per-use parallel rendering. Open AWS setup below to connect your account.';
  $('s_aws_profile').value = CFG.aws_profile||'';
  $('s_aws_region').value = CFG.aws_region||'us-east-1';
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
  $('s_ffstate').textContent = !CFG.ffmpeg ? 'FFmpeg is missing. See SETUP.md to install and check it.'
    : miss.length ? ('FFmpeg is missing: '+miss.join(', ')+'. See SETUP.md for a compatible build.')
    : ('ffmpeg found at '+CFG.ffmpeg);
  $('s_ffstate').style.color = (!CFG.ffmpeg || miss.length) ? 'var(--warn)' : 'var(--dim)';
  $('s_path').textContent = 'Suno Studio v' + (CFG.version||'?') + '  ·  settings stored in ' + CFG.config_path;
  providerChanged();
  dlg.showModal();
}

let awsChecked = null;
function openAwsProgress(){
  if(!awsdlg.open) awsdlg.showModal();
  refreshAwsSetup();
}
function awsAccountChanged(){
  $('s_aws_setup').disabled = !awsChecked ||
    awsChecked.profile!==$('s_aws_profile').value.trim() ||
    awsChecked.region!==$('s_aws_region').value.trim();
}
async function refreshAwsSetup(){
  if(!awsdlg.open) return;
  try{
    const state=await (await fetch('/api/aws/setup')).json();
    $('aws_profiles').innerHTML=(state.profiles||[]).map(p=>`<option value="${esc(p)}"></option>`).join('');
    awsChecked=state.account ? {account:state.account,profile:state.checked_profile,region:state.checked_region} : null;
    awsAccountChanged();
    $('s_aws_account').textContent=state.account
      ? `Checked account ${state.account} in ${state.checked_region}. AWS resources will be created there.`
      : state.status==='idle' ? 'Check the AWS account before creating paid resources.' : '';
    $('s_aws_log').textContent=(state.lines||[]).join('\n') ||
      ({signing_in:'Waiting for AWS sign-in…',checking:'Checking AWS account…',
        setting_up:'Creating AWS resources…'}[state.status]||'');
    if(state.ready && !CFG.aws_ready){
      CFG=await (await fetch('/api/config')).json();
      $('s_backend').querySelector('option[value="aws"]').disabled=false;
      $('s_backend_state').textContent=`AWS configured in ${CFG.aws_region}. Select AWS above and Save to use cloud rendering.`;
    }
    if(['signing_in','checking','setting_up'].includes(state.status)) setTimeout(refreshAwsSetup,1500);
  }catch(e){ $('s_aws_log').textContent='Could not read AWS setup status: '+e.message; }
}
async function awsStep(action){
  if(!awsdlg.open) awsdlg.showModal();
  $('s_aws_setup').disabled=true;
  $('s_aws_log').textContent='Starting '+action+'…';
  try{
    const result=await (await fetch('/api/aws/setup',{method:'POST',
      headers:{'Content-Type':'application/json','X-Suno-Setup':'1'},
      body:JSON.stringify({action,profile:$('s_aws_profile').value.trim(),
        region:$('s_aws_region').value.trim()})})).json();
    if(result.error){ $('s_aws_log').textContent=result.error; awsAccountChanged(); return; }
    refreshAwsSetup();
  }catch(e){ $('s_aws_log').textContent='Could not start AWS setup: '+e.message; awsAccountChanged(); }
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
                image_prompt_fragments:readFragments(),
                auto_generate:$('s_auto').checked, allowed_senders:$('s_as').value,
                video_height:$('s_vh').value, render_backend:$('s_backend').value,
                aws_render_size:$('s_aws_size').value,
                visualizer:$('s_vis').value, video_dir:$('s_vd').value, alerts_enabled:$('s_al').checked,
                lyric_aligner:$('s_align').value,
                hybrid_repair:$('s_repair').value,
                kie_low_credits:$('s_alk').value, bg_source:$('s_bg').value, art_title:$('s_arttitle').checked,
                shimmer:$('s_shimmer').checked,
                interlude_mode:$('s_interlude').checked,
                lyric_focus_band:$('s_focus').checked,
                openai_image_model:$('s_im').value,
                auto_video:$('s_av').checked};
  if($('s_gp').value.trim()) body.gmail_app_password = $('s_gp').value.trim();
  if($('s_slack_token').value.trim()) body.slack_bot_token = $('s_slack_token').value.trim();
  if($('s_ok').value.trim()) body.openai_key = $('s_ok').value.trim();
  if($('s_tdt').value.trim()) body.todoist_token = $('s_tdt').value.trim();
  if($('s_key').value.trim()) body[id+'_key'] = $('s_key').value.trim();
  const saved = await (await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(saved.error){ alert(saved.error); return; }
  dlg.close(); await loadConfig();
}

syncManualDeliveryFields();
loadConfig().then(refresh);
setInterval(refresh, 3000);
setInterval(refreshRenderStatus, 60000);
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
