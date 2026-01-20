@echo off
REM Bloomberg Remote Server - Windows Batch Launcher

echo ========================================
echo   Bloomberg Remote Server
echo ========================================
echo.

REM Default settings
set HOST=0.0.0.0
set PORT=8000

REM Parse arguments
:parse_args
if "%1"=="" goto :start
if "%1"=="--host" (
    set HOST=%2
    shift
    shift
    goto :parse_args
)
if "%1"=="--port" (
    set PORT=%2
    shift
    shift
    goto :parse_args
)
if "%1"=="--debug" (
    set BLPREMOTE_DEBUG=true
    shift
    goto :parse_args
)
shift
goto :parse_args

:start
REM Check for virtual environment
if exist "%~dp0..\venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call "%~dp0..\venv\Scripts\activate.bat"
) else (
    echo No virtual environment found. Using system Python.
)

REM Set environment variables
set BLPREMOTE_HOST=%HOST%
set BLPREMOTE_PORT=%PORT%

REM Check if blpapi is available
echo.
echo Checking Bloomberg API availability...
python -c "import blpapi; print('available')" 2>nul
if %ERRORLEVEL% equ 0 (
    echo Bloomberg API is available
) else (
    echo Bloomberg API not available - using mock mode
)

REM Install dependencies
echo.
echo Installing dependencies...
pip install -q fastapi uvicorn pydantic pydantic-settings python-jose passlib bcrypt

REM Start server
echo.
echo Starting server on http://%HOST%:%PORT%
echo Press Ctrl+C to stop
echo.

python -m uvicorn blpremote_server.app:app --host %HOST% --port %PORT%
