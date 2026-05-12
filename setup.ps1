# setup.ps1 -- one-shot bloom-connect server bring-up on Windows.
#
# Idempotent. Safe to run repeatedly. By default, if the server is
# already serving /health on the chosen port, this script is a no-op
# (use -Force to restart anyway).
#
# What it does:
#   1. Find a real Python (skips the Microsoft Store alias trap).
#   2. Create .venv if missing.
#   3. Add C:\blp\DAPI to PATH for this session (blpapi DLL discovery).
#   4. Install / refresh blpapi + blpremote-server (skip with -SkipInstall).
#   5. Start uvicorn detached, wait for /health == healthy.
#   6. Start ngrok detached (or LAN-only with -NoNgrok), poll local API
#      for the public URL.
#   7. Stash URL in .coord/last_ngrok_url.txt and (optional) post it
#      on the live coord channel via tools/coord.py.
#
# Examples:
#   .\setup.ps1                       # full bring-up, post URL nowhere
#   .\setup.ps1 -CoordSend mac        # also post URL to mac via coord
#   .\setup.ps1 -NoNgrok              # LAN deployment, skip ngrok
#   .\setup.ps1 -Force                # restart even if already healthy
#   .\setup.ps1 -SkipInstall -Force   # quick restart, don't touch deps

[CmdletBinding()]
param(
    [switch] $NoNgrok,
    [switch] $SkipInstall,
    [switch] $Force,
    [string] $Port = "8000",
    [string] $CoordSend = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Write-Step($n, $text, $color = "Cyan") {
    Write-Host "[$n] $text" -ForegroundColor $color
}

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
            try {
                $v = & $p --version 2>&1
                if ($LASTEXITCODE -eq 0) { return $p }
            } catch { }
        }
    }
    # Fallback to PATH but reject the Microsoft Store alias which UAC-prompts
    # and silently does nothing.
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") {
        return $cmd.Source
    }
    throw "no working Python 3.10+ found. install from python.org or anaconda."
}

function Test-Health($port) {
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
        return ($h.status -eq "healthy" -and $h.bloomberg_connected)
    } catch {
        return $false
    }
}

# Short-circuit if already healthy and not forced.
if (-not $Force -and (Test-Health $Port)) {
    Write-Host "server already healthy on port $Port -- pass -Force to restart" -ForegroundColor Green
    if (Test-Path "$PSScriptRoot\.coord\last_ngrok_url.txt") {
        $url = (Get-Content "$PSScriptRoot\.coord\last_ngrok_url.txt" -Raw).Trim()
        Write-Host "last published ngrok URL: $url" -ForegroundColor Gray
    }
    return
}

# 1. Python
if (-not (Test-Path ".venv")) {
    $py = Find-Python
    Write-Step "1/6" "no .venv found -- creating from $py"
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
} else {
    Write-Step "1/6" ".venv exists" "Green"
}
$venvPy = (Resolve-Path ".venv\Scripts\python.exe").Path

# 2. blpapi DLL discovery
if (Test-Path "C:\blp\DAPI") {
    if ($env:PATH -notmatch [regex]::Escape("C:\blp\DAPI")) {
        $env:PATH = "C:\blp\DAPI;$env:PATH"
        Write-Step "2/6" "added C:\blp\DAPI to PATH (blpapi DLL discovery)"
    } else {
        Write-Step "2/6" "C:\blp\DAPI already on PATH" "Green"
    }
} else {
    Write-Step "2/6" "C:\blp\DAPI not present -- Bloomberg Terminal not installed?" "Yellow"
}

# 3. Dependencies
if (-not $SkipInstall) {
    Write-Step "3/6" "installing / refreshing blpapi + blpremote-server"
    & $venvPy -m pip install --upgrade pip --quiet 2>&1 | Out-Null
    & $venvPy -m pip install --quiet `
        --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ `
        blpapi 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "blpapi install failed" }
    & $venvPy -m pip install --quiet -e packages/blpremote_server 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "blpremote-server install failed" }
} else {
    Write-Step "3/6" "-SkipInstall passed -- skipping pip" "Green"
}

# 4. Free the port
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    $procIds = $listening.OwningProcess | Select-Object -Unique
    foreach ($procId in $procIds) {
        Write-Step "4/6" "port $Port in use by PID $procId -- terminating" "Yellow"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
} else {
    Write-Step "4/6" "port $Port free" "Green"
}

# 4b. M5(A): default-secret guard. setup.ps1 is a dev bring-up; if
# the caller hasn't picked a real BLPREMOTE_SECRET_KEY and hasn't
# explicitly chosen on ALLOW_DEFAULT_SECRET, opt the process into
# the loud-warning sentinel path so the server boots. Production
# deployments set BLPREMOTE_SECRET_KEY upstream and skip this branch.
if (-not $env:BLPREMOTE_SECRET_KEY -and -not $env:BLPREMOTE_ALLOW_DEFAULT_SECRET) {
    $env:BLPREMOTE_ALLOW_DEFAULT_SECRET = "true"
    Write-Host "      BLPREMOTE_ALLOW_DEFAULT_SECRET=true (dev opt-in; set BLPREMOTE_SECRET_KEY to silence)" -ForegroundColor Yellow
}

# 5. Start uvicorn detached
Write-Step "5/6" "starting uvicorn on 0.0.0.0:$Port"
$uvLog = Join-Path $env:TEMP "blpremote-uvicorn.log"
$uvicorn = Start-Process -FilePath $venvPy `
    -ArgumentList @("-m", "uvicorn", "blpremote_server.app:app", "--host", "0.0.0.0", "--port", $Port) `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $uvLog -RedirectStandardError "$uvLog.err"
Write-Host "      uvicorn pid=$($uvicorn.Id), log=$uvLog" -ForegroundColor Gray

$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Health $Port) { $ready = $true; break }
}
if (-not $ready) {
    throw "server failed to reach healthy/connected on /health within 20s. check $uvLog"
}
Write-Host "      /health = healthy + bloomberg_connected" -ForegroundColor Green

# 6. ngrok + URL publish
if ($NoNgrok) {
    $lan = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*' } |
            Select-Object -First 1).IPAddress
    Write-Step "6/6" "-NoNgrok -- LAN URL: http://${lan}:$Port" "Green"
    return
}

# Resolve ngrok binary, downloading if needed (M10 chunk a).
# Search order:
#   1. tools\ngrok\ngrok.exe (canonical install path)
#   2. tools\ngrok.exe       (back-compat for manual installs)
#   3. download from official mirror
$ngrok = Join-Path $PSScriptRoot "tools\ngrok\ngrok.exe"
if (-not (Test-Path $ngrok)) {
    $ngrokAlt = Join-Path $PSScriptRoot "tools\ngrok.exe"
    if (Test-Path $ngrokAlt) {
        $ngrok = $ngrokAlt
    } else {
        Write-Step "6/6" "ngrok not found -- downloading from official mirror"
        $ngrokDir = Join-Path $PSScriptRoot "tools\ngrok"
        New-Item -ItemType Directory -Path $ngrokDir -Force | Out-Null
        $zipPath = Join-Path $env:TEMP "ngrok-v3-windows.zip"
        $dlUrl = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
        try {
            # ProgressPreference=SilentlyContinue speeds Invoke-WebRequest
            # ~10x on PS5 by skipping the progress-bar render.
            $prevProgress = $ProgressPreference
            $ProgressPreference = "SilentlyContinue"
            Invoke-WebRequest -Uri $dlUrl -OutFile $zipPath -UseBasicParsing
            $ProgressPreference = $prevProgress
            Expand-Archive -Path $zipPath -DestinationPath $ngrokDir -Force
        } finally {
            Remove-Item $zipPath -ErrorAction SilentlyContinue
        }
        $ngrok = Join-Path $ngrokDir "ngrok.exe"
        if (-not (Test-Path $ngrok)) {
            throw "ngrok download succeeded but ngrok.exe not found in $ngrokDir"
        }
        $ver = (& $ngrok --version 2>&1 | Out-String).Trim()
        Write-Host "      installed: $ver" -ForegroundColor Gray
    }
}

# Authtoken bootstrap (M10 chunk b). ngrok 3 stores config at
# $LOCALAPPDATA\ngrok\ngrok.yml on Windows. Probe the file directly
# instead of `ngrok config check` (which returns 0 even without a
# token, just warns to stderr). If absent, open the dashboard in the
# user's browser and read the pasted token from the console.
$ngrokCfg = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.yml"
$hasAuthToken = (Test-Path $ngrokCfg) -and `
    ((Get-Content $ngrokCfg -Raw -ErrorAction SilentlyContinue) -match "(?m)^\s*authtoken:")
if (-not $hasAuthToken) {
    Write-Host "      ngrok needs an authtoken; opening dashboard..." -ForegroundColor Yellow
    Start-Process "https://dashboard.ngrok.com/get-started/your-authtoken"
    $token = Read-Host -Prompt "      paste your ngrok authtoken"
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "no authtoken provided -- aborting (re-run setup.ps1 to retry)"
    }
    & $ngrok config add-authtoken $token.Trim() | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ngrok config add-authtoken failed (exit $LASTEXITCODE)" }
    Write-Host "      authtoken saved to $ngrokCfg" -ForegroundColor Green
}

# Reset any existing ngrok process so we get a fresh tunnel.
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

$ngrokLog = Join-Path $env:TEMP "blpremote-ngrok.log"
$ngrokProc = Start-Process -FilePath $ngrok `
    -ArgumentList @("http", $Port, "--log=stdout") `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $ngrokLog -RedirectStandardError "$ngrokLog.err"

$publicUrl = $null
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    try {
        $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2
        $publicUrl = ($tunnels.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1).public_url
        if (-not $publicUrl -and $tunnels.tunnels) { $publicUrl = $tunnels.tunnels[0].public_url }
        if ($publicUrl) { break }
    } catch { }
}
if (-not $publicUrl) { throw "ngrok started but no public URL surfaced after 15s. check $ngrokLog" }

Write-Step "6/6" "ngrok up: $publicUrl" "Green"

# Stash for next time.
$coordDir = Join-Path $PSScriptRoot ".coord"
if (Test-Path $coordDir) {
    $publicUrl | Out-File -Encoding utf8 (Join-Path $coordDir "last_ngrok_url.txt")
}

# Optional: auto-post on the live coord channel.
if ($CoordSend) {
    $cfg = Join-Path $env:USERPROFILE ".blpremote\coord.json"
    if (-not (Test-Path $cfg)) {
        Write-Host "  -CoordSend $CoordSend requested but $cfg missing -- skip" -ForegroundColor Yellow
    } else {
        $env:PYTHONIOENCODING = "utf-8"
        $body = "server up at $publicUrl (setup.ps1 auto-post; ngrok URL changes on every restart)"
        $tmp = [IO.Path]::GetTempFileName()
        $body | Out-File -Encoding utf8 $tmp
        & $venvPy (Join-Path $PSScriptRoot "tools\coord.py") send $CoordSend --file $tmp 2>&1 | ForEach-Object {
            Write-Host "      coord: $_" -ForegroundColor Gray
        }
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ngrok URL:    $publicUrl" -ForegroundColor Green
Write-Host "  uvicorn pid:  $($uvicorn.Id)" -ForegroundColor Green
Write-Host "  ngrok pid:    $($ngrokProc.Id)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "to stop both:  Stop-Process -Id $($uvicorn.Id), $($ngrokProc.Id) -Force" -ForegroundColor Gray
