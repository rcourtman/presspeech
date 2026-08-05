@echo off
cd /d "%~dp0"
start "" "%~dp0\.venv\Scripts\pythonw.exe" "%~dp0\app.py"
