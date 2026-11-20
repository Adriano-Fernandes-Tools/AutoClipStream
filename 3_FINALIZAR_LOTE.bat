@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1 && (py "scripts\finalizar_lote.py" --todos & goto :end)
python "scripts\finalizar_lote.py" --todos
:end
pause
