# Bloomberg Remote Server - PowerShell Launcher
# This script starts the FastAPI server with Uvicorn

param(
    [string]$Host = "0.0.0.0",
    [int]$Port = 8000,
    [switch]$Debug
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Bloomberg Remote Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if Python is installed
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Python is not installed or not in PATH" -ForegroundColor Red
    exit 1
}

# Check if we're in a virtual environment
$venvPath = Join-Path $PSScriptRoot "..\venv"
$venvActivate = Join-Path $venvPath "Scripts\Activate.ps1"

if (Test-Path $venvActivate) {
    Write-Host "Activating virtual environment..." -ForegroundColor Yellow
    & $venvActivate
} else {
    Write-Host "No virtual environment found. Using system Python." -ForegroundColor Yellow
    Write-Host "Consider creating a venv: python -m venv venv" -ForegroundColor Gray
}

# Set environment variables
$env:BLPREMOTE_HOST = $Host
$env:BLPREMOTE_PORT = $Port

if ($Debug) {
    $env:BLPREMOTE_DEBUG = "true"
}

# Check if blpapi is available
Write-Host ""
Write-Host "Checking Bloomberg API availability..." -ForegroundColor Yellow
$blpapiCheck = python -c "import blpapi; print('available')" 2>&1
if ($blpapiCheck -eq "available") {
    Write-Host "✓ Bloomberg API (blpapi) is available" -ForegroundColor Green
} else {
    Write-Host "⚠ Bloomberg API (blpapi) not available - using mock mode" -ForegroundColor Yellow
    Write-Host "  Install with: pip install blpapi" -ForegroundColor Gray
}

# Install dependencies if needed
Write-Host ""
Write-Host "Checking dependencies..." -ForegroundColor Yellow
pip install -q fastapi uvicorn pydantic pydantic-settings python-jose passlib bcrypt

# Start the server
Write-Host ""
Write-Host "Starting server on http://${Host}:${Port}" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

$reloadFlag = if ($Debug) { "--reload" } else { "" }

python -m uvicorn blpremote_server.app:app --host $Host --port $Port $reloadFlag
