#!/bin/bash
# Build a standalone "Bloomberg Remote.app" + .dmg installer for macOS.
#
# Output:
#   build_artifacts/dist/Bloomberg Remote.app   the bundle (~40 MB)
#   build_artifacts/Bloomberg Remote.dmg        the drag-to-install dmg
#
# Self-contained: creates an isolated .venv-build so PyInstaller doesn't
# pick up unrelated site-packages from the system Python. Without this
# the bundle ballooned to 2 GB on the first attempt (anaconda
# site-packages dragged in yt_dlp, requests, mutagen, …). Clean venv
# brings it back to a normal app size.
#
# Run from repo root:
#   ./scripts/build_mac_app.sh
#
# Requires: python ≥3.10, hdiutil (built into macOS).
# No code signing — first launch shows Gatekeeper "verifying" then
# the standard unidentified-developer warning. Right-click → Open
# to bypass once, then the OS remembers. Real signing needs an Apple
# Developer ID ($99/yr); skipped for v1 single-user use.

set -euo pipefail
cd "$(dirname "$0")/.."
HERE="$(pwd)"

step() { printf "\033[1;36m[%s]\033[0m %s\n" "$1" "$2"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$1"; }

# ── 1. Clean build venv ──────────────────────────────────────────
step 1 "creating clean build venv at .venv-build"
rm -rf .venv-build
python3 -m venv .venv-build
.venv-build/bin/pip install --quiet --upgrade pip
.venv-build/bin/pip install --quiet -e "packages/blpremote_client[llm]" pyinstaller
ok "build venv ready ($(du -sh .venv-build | cut -f1))"

# ── 2. PyInstaller bundle ─────────────────────────────────────────
step 2 "bundling .app via PyInstaller"
rm -rf build_artifacts
mkdir -p build_artifacts
cd build_artifacts
../.venv-build/bin/pyinstaller --noconfirm --windowed \
    --name "Bloomberg Remote" \
    --osx-bundle-identifier dev.krishna.bloomconnect \
    --hidden-import blpremote_client.ui \
    --hidden-import blpremote_client.host \
    --hidden-import blpremote_client.llm \
    --collect-submodules openai \
    ../tools/blpremote-client-ui.py \
    > pyinstaller.log 2>&1 || { tail -30 pyinstaller.log; exit 1; }
APP="dist/Bloomberg Remote.app"
test -d "$APP"
ok "bundle built ($(du -sh "$APP" | cut -f1))"

# ── 3. DMG installer ──────────────────────────────────────────────
step 3 "wrapping in .dmg installer"
# Standard "drag the app to /Applications" affordance.
DMG_DIR="$(mktemp -d)/dmg-staging"
mkdir -p "$DMG_DIR"
cp -R "$APP" "$DMG_DIR/"
ln -s /Applications "$DMG_DIR/Applications"
hdiutil create -volname "Bloomberg Remote" \
    -srcfolder "$DMG_DIR" \
    -ov -format UDZO \
    "Bloomberg Remote.dmg" > hdiutil.log 2>&1
rm -rf "$(dirname "$DMG_DIR")"
ok "dmg created ($(du -sh "Bloomberg Remote.dmg" | cut -f1))"

cd "$HERE"
echo
ok "build complete"
echo "  • App:     build_artifacts/dist/Bloomberg Remote.app"
echo "  • Installer: build_artifacts/Bloomberg Remote.dmg"
echo ""
echo "Install: open the dmg, drag the app to Applications, eject."
echo "First launch: right-click the app → Open to bypass the unsigned-dev warning once."
