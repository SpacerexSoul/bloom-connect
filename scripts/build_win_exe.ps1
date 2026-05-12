# Build a standalone "Bloomberg Remote Server.exe" installer for Windows.
#
# Mirror of scripts/build_mac_app.sh on the server side. Owned by
# win (sister-side); this is the recipe / spec, win runs it on a
# real Windows box with Bloomberg installed.
#
# Output:
#   build_artifacts\dist\Bloomberg Remote Server\         the bundle dir
#   build_artifacts\Bloomberg-Remote-Server-Setup.exe     Inno Setup installer
#
# Self-contained: creates an isolated .venv-build so PyInstaller
# doesn't pick up unrelated site-packages. Same bloat lesson as
# the Mac side (2 GB -> 40 MB).
#
# Run from repo root in an elevated PowerShell:
#   .\scripts\build_win_exe.ps1
#
# Requires: Python 3.10+, Inno Setup 6 (chocolatey: choco install innosetup).
# No code signing — first launch shows SmartScreen "unrecognised app"
# warning; user clicks "More info" → "Run anyway". Real signing
# needs an EV cert (~$200/yr); skipped for v1 single-user use.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$Here = (Get-Location).Path

function Write-Step { param($n, $text) Write-Host "[$n] $text" -ForegroundColor Cyan }
function Write-Ok   { param($text) Write-Host "OK $text" -ForegroundColor Green }

# 1. Clean build venv
Write-Step 1 "creating clean build venv at .venv-build"
Remove-Item -Recurse -Force .venv-build -ErrorAction SilentlyContinue
python -m venv .venv-build
& .venv-build\Scripts\pip.exe install --quiet --upgrade pip
& .venv-build\Scripts\pip.exe install --quiet -e "packages\blpremote_server" pyinstaller
Write-Ok "build venv ready"

# 2. PyInstaller bundle
Write-Step 2 "bundling .exe via PyInstaller"
Remove-Item -Recurse -Force build_artifacts -ErrorAction SilentlyContinue
New-Item -ItemType Directory build_artifacts | Out-Null
Push-Location build_artifacts

$IconFlag = @()
$IconPath = "..\docs\icons\bloom-connect.ico"
if (Test-Path $IconPath) {
    $IconFlag = @("--icon", $IconPath)
}

& ..\.venv-build\Scripts\pyinstaller.exe --noconfirm --windowed `
    --name "Bloomberg Remote Server" `
    @IconFlag `
    --hidden-import blpremote_server.ui `
    --hidden-import blpremote_server.app `
    --hidden-import blpremote_server.session `
    --collect-submodules blpremote_server `
    ..\tools\blpremote-server-ui.py

Pop-Location
Write-Ok "bundle built"

# 3. Inno Setup installer (Windows equivalent of a .dmg)
# Requires Inno Setup; if absent, ship the bundle dir for users to
# copy manually.
$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (Test-Path $ISCC) {
    Write-Step 3 "wrapping in Inno Setup installer"
    @"
[Setup]
AppName=Bloomberg Remote Server
AppVersion=0.1.0
DefaultDirName={autopf}\Bloomberg Remote Server
DefaultGroupName=Bloomberg Remote
OutputDir=$Here\build_artifacts
OutputBaseFilename=Bloomberg-Remote-Server-Setup
SetupIconFile=
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
[Files]
Source: "$Here\build_artifacts\dist\Bloomberg Remote Server\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Bloomberg Remote Server"; Filename: "{app}\Bloomberg Remote Server.exe"
Name: "{commondesktop}\Bloomberg Remote Server"; Filename: "{app}\Bloomberg Remote Server.exe"
[Run]
Filename: "{app}\Bloomberg Remote Server.exe"; Description: "Launch now"; Flags: postinstall nowait skipifsilent
"@ | Out-File -Encoding ascii -FilePath build_artifacts\installer.iss
    & $ISCC build_artifacts\installer.iss
    Write-Ok "installer built"
} else {
    Write-Host "Inno Setup not found — installer step skipped. Install with: choco install innosetup" -ForegroundColor Yellow
}

Write-Host ""
Write-Ok "build complete"
Write-Host "  Bundle:    build_artifacts\dist\Bloomberg Remote Server\"
Write-Host "  Installer: build_artifacts\Bloomberg-Remote-Server-Setup.exe (if Inno Setup present)"
