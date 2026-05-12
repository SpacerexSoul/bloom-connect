#!/bin/bash
# setup.sh — one-shot bloom-connect client bring-up on macOS.
#
# Idempotent. Safe to run repeatedly. Mirrors setup.ps1 on the
# Windows server side: detect Python, create venv, install the
# client package with all extras, bootstrap ~/.blpremote/ files,
# and drop Connect.command on the Desktop for double-click access.
#
# What it does:
#   1. Find Python ≥3.10 (prefers python3.12 / python3.11 / python3).
#   2. Create .venv at repo root if missing.
#   3. Install blpremote_client[llm,pandas,polars] into the venv.
#   4. Bootstrap ~/.blpremote/identity.json + openrouter.json
#      skeletons if absent. Never overwrites existing files.
#   5. Prompt for an OpenRouter API key if openrouter.json is empty.
#   6. Symlink Connect.command to ~/Desktop for one-click launch.
#
# Examples:
#   ./setup.sh                  # full install
#   ./setup.sh --skip-install   # just bootstrap files + Desktop link
#   ./setup.sh --no-desktop     # skip the Desktop symlink

set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

SKIP_INSTALL=0
NO_DESKTOP=0
for arg in "$@"; do
    case "$arg" in
        --skip-install) SKIP_INSTALL=1 ;;
        --no-desktop)   NO_DESKTOP=1 ;;
        -h|--help)
            sed -n '1,28p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

step() { printf "\033[1;36m[%s]\033[0m %s\n" "$1" "$2"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$1"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$1"; }

# ── 1. Find Python ──────────────────────────────────────────────
step 1 "finding python ≥3.10"
PYTHON=""
for cand in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver=$("$cand" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=${ver%.*}; minor=${ver#*.}
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON=$(command -v "$cand")
            ok "found $PYTHON (python $ver)"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    echo "no python 3.10+ on PATH — install from python.org or homebrew" >&2
    exit 1
fi

# ── 2. venv ─────────────────────────────────────────────────────
step 2 "creating venv at .venv"
if [ -x ".venv/bin/python" ]; then
    ok "venv already exists"
else
    "$PYTHON" -m venv .venv
    ok "created .venv"
fi
VENV_PY=".venv/bin/python"

# ── 3. Install package ──────────────────────────────────────────
if [ "$SKIP_INSTALL" -eq 0 ]; then
    step 3 "installing blpremote_client[llm,pandas,polars]"
    "$VENV_PY" -m pip install --upgrade pip --quiet
    "$VENV_PY" -m pip install -e "packages/blpremote_client[llm,pandas,polars]" --quiet
    ok "client package installed"
else
    step 3 "skipping install (--skip-install)"
fi

# ── 4. Bootstrap ~/.blpremote/ ──────────────────────────────────
step 4 "bootstrapping ~/.blpremote/"
mkdir -p "$HOME/.blpremote"
chmod 700 "$HOME/.blpremote"

IDENTITY="$HOME/.blpremote/identity.json"
if [ ! -f "$IDENTITY" ]; then
    cat > "$IDENTITY" <<'JSON'
{
  "url": "",
  "user": "mac",
  "password": ""
}
JSON
    chmod 600 "$IDENTITY"
    warn "wrote identity stub at $IDENTITY — fill in url + password before connecting"
else
    ok "identity.json already present"
fi

OPENROUTER="$HOME/.blpremote/openrouter.json"
if [ ! -f "$OPENROUTER" ]; then
    cat > "$OPENROUTER" <<'JSON'
{
  "api_key": ""
}
JSON
    chmod 600 "$OPENROUTER"
fi

# ── 5. Prompt for OpenRouter key if blank ───────────────────────
step 5 "checking OpenRouter key"
key_present=$("$VENV_PY" -c "
import json, sys
try:
    d = json.load(open('$OPENROUTER'))
    print('y' if isinstance(d, dict) and d.get('api_key') else 'n')
except Exception:
    print('n')
")
if [ "$key_present" = "y" ]; then
    ok "OpenRouter key present"
elif [ -t 0 ]; then
    printf "paste an OpenRouter key (sk-or-…) or press enter to skip: "
    read -r KEY
    if [ -n "$KEY" ]; then
        "$VENV_PY" -c "
import json
p='$OPENROUTER'
json.dump({'api_key': '$KEY'}, open(p,'w'))
"
        ok "wrote OpenRouter key to $OPENROUTER"
    else
        warn "OpenRouter key not set — Test-query pane in the UI will stay disabled until you write one"
    fi
else
    warn "no tty — OpenRouter key not set; write it to $OPENROUTER manually"
fi

# ── 6. Desktop launcher ─────────────────────────────────────────
if [ "$NO_DESKTOP" -eq 0 ]; then
    step 6 "linking Connect.command to ~/Desktop"
    DESKTOP_LINK="$HOME/Desktop/Connect.command"
    if [ -L "$DESKTOP_LINK" ] || [ -f "$DESKTOP_LINK" ]; then
        ok "Desktop launcher already present"
    else
        ln -s "$HERE/Connect.command" "$DESKTOP_LINK"
        ok "linked $DESKTOP_LINK -> $HERE/Connect.command"
    fi
fi

echo
ok "setup complete"
echo "  • Edit $IDENTITY with your server URL + password (if not already done)"
echo "  • Double-click ~/Desktop/Connect.command to launch the UI"
echo "  • Or run: .venv/bin/python tools/blpremote-client-ui.py"
