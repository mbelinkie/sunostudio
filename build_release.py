#!/usr/bin/env python3
"""Build deterministic macOS app and source release ZIPs."""

import argparse
import plistlib
import re
import stat
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION = "6.0.8"
SOURCE_FILES = (
    "README.md", "SETUP.md", "SECURITY.md", "LICENSE", "lyrics-parsing.md",
    "suno_studio.py", "bug_reports.py", "stable_ts_hybrid.py", "subs_doctor.py",
    "video_doctor.py", "aws_link.py", "aws_render.py", "aws_worker.py",
    "setup_aws.py", "Dockerfile.aws", "requirements-cloud.txt", "build_release.py",
    "Start Suno Studio.command", "Start Suno Studio.bat", "Check Subtitles.command",
    "Diagnose Video.command", "Check Video Tools.ps1",
    "Install Local Lyric Alignment.command",
    "test_aws_sso.py", "test_aws_sso_refresh.py",
    "test_aws_app.py", "test_aws_render.py", "test_app_reliability.py",
    "test_finished_media.py", "test_media_purge.py",
)
EPOCH = (1980, 1, 1, 0, 0, 0)


def _bundle_launcher():
    return b'''#!/bin/bash\nset -u\nROOT="$(cd "$(dirname "$0")/../Resources" && pwd)"\ncd "$ROOT" || exit 1\nPYTHON=""\nfor CANDIDATE in /usr/local/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do\n  if [ -x "$CANDIDATE" ] && "$CANDIDATE" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then\n    PYTHON="$CANDIDATE"\n    break\n  fi\ndone\nif [ -z "$PYTHON" ]; then\n  osascript -e 'display dialog "Python 3.9 or later is required. Install Python, then open Suno Studio again." buttons {"OK"}'\n  exit 1\nfi\nexec "$PYTHON" suno_studio.py\n'''


def _files(version):
    files = {}
    for name in SOURCE_FILES:
        path = ROOT / name
        if not path.is_file():
            raise FileNotFoundError(f"release input missing: {name}")
        files[f"Suno-Studio-{version}/{name}"] = (path.read_bytes(), path.stat().st_mode)
    return files


def _app_files(version, source_files):
    bundle = f"Suno Studio {version}.app/Contents"
    files = {}
    for archive_path, (content, mode) in source_files.items():
        relative = archive_path.split("/", 1)[1]
        files[f"{bundle}/Resources/{relative}"] = (content, mode)
    files[f"{bundle}/MacOS/SunoStudio"] = (_bundle_launcher(), 0o100755)
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": "SunoStudio",
        "CFBundleIdentifier": "com.sunostudio.app",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Suno Studio",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "10.15",
        "NSHighResolutionCapable": True,
    }
    files[f"{bundle}/Info.plist"] = (plistlib.dumps(info, fmt=plistlib.FMT_XML, sort_keys=True), 0o100644)
    files[f"{bundle}/PkgInfo"] = (b"APPL????", 0o100644)
    for name in ("README.md", "SETUP.md", "LICENSE"):
        files[name] = source_files[f"Suno-Studio-{version}/{name}"]
    return files


def _write_zip(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            content, mode = files[name]
            info = zipfile.ZipInfo(name, EPOCH)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | stat.S_IMODE(mode)) << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _check_app_zip(path, version, source_files):
    root = f"Suno Studio {version}.app/Contents/Resources/"
    with zipfile.ZipFile(path) as archive:
        for name in SOURCE_FILES:
            if (name.endswith(".py") and
                    archive.read(root + name) != source_files[f"Suno-Studio-{version}/{name}"][0]):
                raise RuntimeError(f"Mac app ZIP does not contain current module: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", args.version):
        parser.error("version must look like 6.0 or 6.0.8")
    source_files = _files(args.version)
    source_version = re.search(
        r'^APP_VERSION\s*=\s*["\']([^"\']+)',
        source_files[f"Suno-Studio-{args.version}/suno_studio.py"][0].decode("utf-8"), re.M)
    if not source_version or source_version.group(1) != args.version:
        parser.error(f"suno_studio.py APP_VERSION must match release version {args.version}")
    source_zip = args.output_dir / f"SunoStudio-{args.version}-source.zip"
    app_zip = args.output_dir / f"SunoStudio-{args.version}-macOS.zip"
    _write_zip(source_zip, source_files)
    _write_zip(app_zip, _app_files(args.version, source_files))
    _check_app_zip(app_zip, args.version, source_files)
    print(source_zip)
    print(app_zip)


if __name__ == "__main__":
    main()
