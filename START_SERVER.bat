@echo off
REM ============================================
REM Bloomberg Remote Server - One-Click Setup
REM ============================================
REM Double-click this file to set up and start the server

title Bloomberg Remote Server Setup
color 0A

echo.
echo ============================================
echo    Bloomberg Remote Server - Quick Setup
echo ============================================
echo.

REM Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Please install Python 3.10+
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] Creating virtual environment...
if not exist ".venv" (
    python -m venv .venv
)

echo [2/4] Activating environment...
call .venv\Scripts\activate.bat

echo [3/4] Installing dependencies...
pip install --quiet --upgrade pip
pip install --quiet -e packages/blpremote_server

echo [4/4] Starting server...
echo.
echo ============================================
echo    Server starting on http://localhost:8000
echo ============================================
echo.
echo Press Ctrl+C to stop the server
echo.

python -m uvicorn blpremote_server.app:app --host 0.0.0.0 --port 8000

pause
