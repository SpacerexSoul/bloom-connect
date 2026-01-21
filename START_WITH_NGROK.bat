@echo off
REM ============================================
REM Bloomberg Remote Server with ngrok tunnel
REM ============================================
REM Double-click this file to start server + ngrok

title Bloomberg Remote Server + ngrok
color 0A

echo.
echo ============================================
echo    Bloomberg Remote Server + ngrok Tunnel
echo ============================================
echo.

REM Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Please install Python 3.10+
    pause
    exit /b 1
)

REM Check for ngrok
ngrok version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] ngrok not found! Please install ngrok:
    echo.
    echo   1. Download from https://ngrok.com/download
    echo   2. Extract ngrok.exe to this folder or add to PATH
    echo   3. Run: ngrok config add-authtoken YOUR_TOKEN
    echo.
    pause
    exit /b 1
)

REM Setup environment if needed
if not exist ".venv" (
    echo [1/3] First run - creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install --quiet --upgrade pip
    pip install --quiet -e packages/blpremote_server
) else (
    call .venv\Scripts\activate.bat
)

echo [1/2] Starting Bloomberg server in background...
start /b cmd /c "python -m uvicorn blpremote_server.app:app --host 0.0.0.0 --port 8000"

REM Wait for server to start
timeout /t 3 /nobreak >nul

echo [2/2] Starting ngrok tunnel...
echo.
echo ============================================
echo    COPY YOUR NGROK URL TO YOUR MAC CLIENT
echo ============================================
echo.
echo Look for the "Forwarding" line below
echo Example: https://abc123.ngrok-free.app
echo.

ngrok http 8000

pause
