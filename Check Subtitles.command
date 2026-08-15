#!/bin/bash
# Double-click to inspect the newest song's subtitles without rendering video.
# Optionally drag an .mp3 onto this file in Terminal to check that song.

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed. Run:  xcode-select --install"
  echo; read -n 1 -s -r -p "Press any key to close..."; exit 1
fi

python3 subs_doctor.py "$@"

echo
read -n 1 -s -r -p "Press any key to close this window..."
