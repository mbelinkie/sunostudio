#!/bin/bash
set -euo pipefail

PY="/usr/local/bin/python3.11"
RUNTIME="$HOME/.suno_studio/stable-ts-venv"

if [ ! -x "$PY" ]; then
  osascript -e 'display alert "Python 3.11 is required" message "Install the Intel Python 3.11 build, then run this installer again." as critical' >/dev/null 2>&1 || true
  exit 1
fi

mkdir -p "$HOME/.suno_studio"
"$PY" -m venv "$RUNTIME"
"$RUNTIME/bin/pip" install \
  "pip==25.1.1" "setuptools<81" wheel \
  "numpy==1.26.4" "llvmlite==0.43.0" "numba==0.60.0" \
  "torch==2.2.2" "stable-ts==2.19.1"
"$RUNTIME/bin/python" -c "import stable_whisper; print('stable-ts runtime ready')"

osascript -e 'display alert "Local lyric alignment is ready" message "Open Suno Studio Settings and choose Local stable-ts hybrid under Lyric timing."' >/dev/null 2>&1 || true
