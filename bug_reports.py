"""Explicit, small Sentry reports for Suno Studio.

Only fixed app metadata and a categorized error reach Sentry. In particular,
job fields, lyrics, paths, provider responses, and media never enter a report.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone


# A maintainer may publish a public Sentry DSN here. End users need no Sentry
# account. An environment override is useful while testing a release.
PUBLIC_SENTRY_DSN = (
    "https://67ae98e1c0d684f629409ecf962d9831"
    "@o4511898592018432.ingest.us.sentry.io/4512164797415424"
)

STAGES = frozenset({
    "setup", "intake", "song", "artwork", "subtitles", "video",
    "delivery", "interface", "other",
})

ERROR_CATEGORIES = (
    (r"\b(?:429|rate.?limit|quota)\b", "Rate limit or quota reached"),
    (r"\b(?:401|403|unauthori[sz]ed|forbidden|permission)\b", "Access denied"),
    (r"\b(?:timeout|timed out)\b", "Operation timed out"),
    (r"\b(?:connection|network|dns)\b", "Network connection failed"),
    (r"\b(?:ffmpeg|ffprobe|encoder|encode)\b", "Video encoding failed"),
    (r"\b(?:upload|download|storage)\b", "File transfer failed"),
    (r"\b(?:invalid|malformed|missing)\b", "Required input was invalid or missing"),
)
SAFE_SUMMARIES = frozenset(summary for _, summary in ERROR_CATEGORIES) | {"Unexpected failure"}


def safe_summary(raw_error):
    """Categorize an error without forwarding its potentially sensitive text."""
    text = str(raw_error or "")[:2000]
    for pattern, summary in ERROR_CATEGORIES:
        if re.search(pattern, text, re.IGNORECASE):
            return summary
    return "Unexpected failure"


def report_preview(stage, raw_error, version):
    if stage not in STAGES:
        stage = "other"
    return {
        "version": str(version)[:40],
        "platform": sys.platform,
        "stage": stage,
        "error_summary": safe_summary(raw_error),
    }


def configured():
    return bool((os.environ.get("SUNO_STUDIO_SENTRY_DSN") or PUBLIC_SENTRY_DSN).strip())


def send_report(preview):
    """Create one Sentry error event and return its reference ID."""
    dsn = (os.environ.get("SUNO_STUDIO_SENTRY_DSN") or PUBLIC_SENTRY_DSN).strip()
    parsed = urllib.parse.urlparse(dsn)
    project_id = parsed.path.rstrip("/").split("/")[-1]
    if parsed.scheme != "https" or not parsed.hostname or not parsed.username or not project_id.isdigit():
        raise RuntimeError("Bug reporting is not configured in this release")
    stage = preview.get("stage") if preview.get("stage") in STAGES else "other"
    summary = preview.get("error_summary")
    if summary not in SAFE_SUMMARIES:
        summary = safe_summary(summary)
    version = re.sub(r"[^0-9A-Za-z._-]", "", str(preview.get("version") or ""))[:40]
    event_id = uuid.uuid4().hex
    event = {
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": "other",
        "level": "error",
        "release": version,
        "message": summary,
        "tags": {"platform": sys.platform, "stage": stage},
        "fingerprint": [stage, summary],
    }
    base_path = parsed.path.rstrip("/").rsplit("/", 1)[0]
    endpoint = f"https://{parsed.hostname}{base_path}/api/{project_id}/store/"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(event, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Sentry-Auth": "Sentry sentry_version=7,sentry_client=suno-studio/1,sentry_key=" + parsed.username,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("Bug report was not accepted")
    return event_id


if __name__ == "__main__":
    assert safe_summary("my secret lyrics; ffmpeg returned 1") == "Video encoding failed"
    assert safe_summary("Bearer abc123; https://private.example") == "Unexpected failure"
    assert report_preview("unknown", "secret", "6.0")["stage"] == "other"
