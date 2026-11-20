@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1 && (py "scripts\gravar_lives.py" & goto :end)
python "scripts\gravar_lives.py"
:end
pause
