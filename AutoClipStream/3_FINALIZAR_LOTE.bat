@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.11 "scripts\finalizar_lote.py" --todos
pause
