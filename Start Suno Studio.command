#!/bin/bash
# Double-click this file to launch Suno Studio.
# Keep it in the same folder as suno_studio.py.

cd "$(dirname "$0")" || exit 1

if [ ! -f "suno_studio.py" ]; then
  echo "Can't find suno_studio.py."
  echo "This launcher has to live in the same folder as the script."
  echo
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed yet."
  echo
  echo "Run this once, click Install when macOS asks, then try again:"
  echo "    xcode-select --install"
  echo
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi

python3 suno_studio.py

echo
echo "Suno Studio stopped."
read -n 1 -s -r -p "Press any key to close this window..."
