@echo off
rem Stop the Vaga server.
cd /d "%~dp0"
python app\launcher.py stop
timeout /t 2 >nul
