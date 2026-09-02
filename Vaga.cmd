@echo off
rem Start Vaga and open it in your browser. Safe to double-click twice.
cd /d "%~dp0"
python app\launcher.py start
if errorlevel 1 pause
