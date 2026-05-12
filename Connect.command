#!/bin/bash
# Double-click launcher for the mac client UI.
# Finder treats .command files as executable shell scripts; this opens
# Terminal, runs python on the UI script, and the Tk window appears.

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Prefer the venv at the repo root if one was created by setup.sh,
# fall back to python3 on the PATH.
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

exec "$PYTHON" tools/blpremote-client-ui.py
