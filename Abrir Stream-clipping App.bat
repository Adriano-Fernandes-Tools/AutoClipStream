@echo off
cd /d "%~dp0"
set "PY_CMD="
where pyw >nul 2>&1 && set "PY_CMD=pyw"
if not defined PY_CMD (
    where py >nul 2>&1 && set "PY_CMD=py"
)
if not defined PY_CMD (
    where pythonw >nul 2>&1 && set "PY_CMD=pythonw"
)
if not defined PY_CMD (
    where python >nul 2>&1 && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python was not found in PATH or system.
    pause
    exit /b 1
)

start "" /min %PY_CMD% "scripts\stream_app.py"
ping -n 3 127.0.0.1 >nul
if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app=http://127.0.0.1:8795 --window-size=1240,780
) else if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files\Microsoft\Edge\Application\msedge.exe" --app=http://127.0.0.1:8795 --window-size=1240,780
) else (
    start "" "http://127.0.0.1:8795"
)
