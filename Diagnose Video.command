#!/bin/bash
# Double-click to diagnose the lyric-video pipeline.
# Optionally drag an .mp3 onto this file in Terminal to test that specific song.

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed. Run:  xcode-select --install"
  echo
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi

python3 video_doctor.py "$@"

echo
read -n 1 -s -r -p "Press any key to close this window..."
