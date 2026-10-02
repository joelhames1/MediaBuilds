#!/usr/bin/env bash
# One-time setup: ./setup.sh   (add --align for Whisper timing, a large download)
set -euo pipefail
cd "$(dirname "$0")"

# Find a Python 3.10+ (macOS's built-in python3 is 3.9, which is too old).
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "songvid needs Python 3.10 or newer, and none was found."
  echo "On a Mac:  brew install python@3.12   then run ./setup.sh again."
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is missing. On a Mac:  brew install ffmpeg   then run ./setup.sh again."
  exit 1
fi

# Rebuild the venv if it was made with an old Python.
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "Removing .venv (it was built with Python $(.venv/bin/python -V 2>&1 | cut -d' ' -f2))."
  rm -rf .venv
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
EXTRAS="ui,dev"
[ "${1:-}" = "--align" ] && EXTRAS="$EXTRAS,align"
.venv/bin/python -m pip install --quiet -e ".[${EXTRAS}]"

echo
echo "Done ($(.venv/bin/python -V)). Start Cuesheet with:"
echo "  source .venv/bin/activate && songvid ui"
