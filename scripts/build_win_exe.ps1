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
# No code signing -- first launch shows SmartScreen "unrecognised app"
# warning; user clicks "More info" -> "Run anyway". Real signing
# needs an EV cert (~$200/yr); skipped for v1 single-user use.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$Here = (Get-Location).Path

function Write-Step { param($n, $text) Write-Host "[$n] $text" -ForegroundColor Cyan }
function Write-Ok   { param($text) Write-Host "OK $text" -ForegroundColor Green }

# Find a real Python -- lifted from setup.ps1 to avoid the Microsoft
# Store python.exe alias trap (UAC-prompts and silently does nothing).
function Find-Python {
    $candidates = @(
        "C:\ProgramData\Anaconda3\envs\rhul_core\python.exe",
        "C:\ProgramData\Anaconda3\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            try { & $p --version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { return $p } } catch { }
        }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { return $cmd.Source }
    throw "no working Python 3.10+ found. install from python.org or anaconda."
}

# 1. Clean build venv
Write-Step 1 "creating clean build venv at .venv-build"
Remove-Item -Recurse -Force .venv-build -ErrorAction SilentlyContinue
$BasePy = Find-Python
Write-Host "    using base python: $BasePy" -ForegroundColor Gray
& $BasePy -m venv .venv-build
# Use `python -m pip` not bare pip.exe — Windows refuses
# pip-upgrades-itself via the launched .exe path with "To modify pip,
# please run the following command" since pip 22+.
& .venv-build\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv-build\Scripts\python.exe -m pip install --quiet -e "packages\blpremote_server" pyinstaller
Write-Ok "build venv ready"

# 2. PyInstaller bundle
Write-Step 2 "bundling .exe via PyInstaller"
Remove-Item -Recurse -Force build_artifacts -ErrorAction SilentlyContinue
New-Item -ItemType Directory build_artifacts | Out-Null
Push-Location build_artifacts

# PyInstaller writes informational messages to stderr (WARNING:
# anaconda detection, etc.). PowerShell with ErrorActionPreference=Stop
# treats stderr writes as fatal NativeCommandError. Use array-splat
# (@pyiArgs) + redirect both streams so stderr writes don't trip the
# Stop action. We then check exit code + bundle dir for success.
$pyiArgs = @(
    "--noconfirm", "--windowed",
    "--name", "Bloomberg Remote Server",
    "--hidden-import", "blpremote_server.ui",
    "--hidden-import", "blpremote_server.app",
    "--hidden-import", "blpremote_server.session_manager",
    "--collect-submodules", "blpremote_server",
    # --collect-all tkinter pulls the Tcl/Tk DLLs + init scripts.
    # Required when the build base python is conda (PyInstaller's
    # default tkinter bundling assumes vanilla python.org layout).
    "--collect-all", "tkinter",
    "..\tools\blpremote-server-ui.py"
)
# App icon — gated on Test-Path so vanilla checkouts (or builds
# before docs/icons/build_icon.py has been run) still succeed.
$IconPath = "..\docs\icons\bloom-connect.ico"
if (Test-Path $IconPath) {
    $pyiArgs = @("--icon", $IconPath) + $pyiArgs
}
# Conda-specific DLL workaround: the venv built from conda python
# inherits DLL discovery from the base env. PyInstaller misses the
# C extensions that live in <conda>\envs\<env>\DLLs (pyexpat,
# unicodedata, etc.). --add-binary copies the whole DLLs/ folder
# into the bundle root so the runtime loader finds them. No-op if
# the build base is vanilla python.org.
$baseDLLs = (& $BasePy -c "import os, sys; print(os.path.join(sys.base_prefix, 'DLLs'))").Trim()
if (Test-Path $baseDLLs) {
    Write-Host "    bundling base-python DLLs from: $baseDLLs" -ForegroundColor Gray
    $pyiArgs += @("--add-binary", "$baseDLLs\*;.")
}
# Drop ErrorActionPreference for this call so PyInstaller's stderr
# writes don't trip Stop. Capture exit code explicitly.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& "..\.venv-build\Scripts\pyinstaller.exe" @pyiArgs *> pyinstaller.log
$pyiExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pyiExit -ne 0 -or -not (Test-Path "dist\Bloomberg Remote Server")) {
    Write-Host "PyInstaller failed (exit=$pyiExit) -- last 30 lines:" -ForegroundColor Red
    Get-Content pyinstaller.log -Tail 30
    throw "bundle did not materialise"
}

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
    Write-Host "Inno Setup not found -- installer step skipped. Install with: choco install innosetup" -ForegroundColor Yellow
}

Write-Host ""
Write-Ok "build complete"
Write-Host "  Bundle:    build_artifacts\dist\Bloomberg Remote Server\"
Write-Host "  Installer: build_artifacts\Bloomberg-Remote-Server-Setup.exe (if Inno Setup present)"
