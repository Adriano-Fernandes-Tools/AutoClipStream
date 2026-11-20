@echo off
cd /d "%~dp0"
where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" /min pyw -3.11 "scripts\stream_app.py"
) else (
    start "" /min py -3.11 "scripts\stream_app.py"
)
timeout /t 1 /nobreak >nul
if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app=http://127.0.0.1:8795 --window-size=1240,780
) else (
    start "" "http://127.0.0.1:8795"
)
