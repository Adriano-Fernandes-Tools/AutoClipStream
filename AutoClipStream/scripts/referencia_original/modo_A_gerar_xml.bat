@echo off
chcp 65001 >nul
py -3.11 "%~dp0modo_A_gerar_xml.py"
if errorlevel 1 pause
