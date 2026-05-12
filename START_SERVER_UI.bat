@echo off
REM ============================================================
REM Bloomberg Remote -- Server UI launcher (M9 chunk d)
REM
REM Double-click this file to open the desktop UI. The UI itself
REM is the controller; it spawns / supervises uvicorn + ngrok via
REM setup.ps1 from its [Start Server] button. So: this launcher
REM never starts the server directly -- only the UI.
REM
REM Why pythonw.exe and not python.exe: pythonw doesn't allocate a
REM console window. Without it Windows would also pop a stray
REM cmd.exe behind the Tk window every double-click, which looks
REM unprofessional and confuses users about whether to close the
REM cmd or the UI.
REM ============================================================

setlocal

REM Hop into the repo root (the .bat sits there).
cd /d "%~dp0"

REM Prefer the venv we built during setup.ps1; fall back to PATH.
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if exist "%VENV_PYW%" (
    set "PY=%VENV_PYW%"
) else if exist "%VENV_PY%" (
    set "PY=%VENV_PY%"
) else (
    where pythonw.exe >nul 2>&1 && (set "PY=pythonw.exe") || (set "PY=python.exe")
)

REM Launch the UI detached (start /B + no /WAIT) so the cmd window
REM that hosts this batch can exit immediately.
start "" "%PY%" "%~dp0tools\blpremote-server-ui.py"

endlocal
