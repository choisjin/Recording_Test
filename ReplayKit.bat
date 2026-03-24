@echo off
cd /d "%~dp0"
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" server.py
) else if exist "venv\Scripts\python.exe" (
    start "" "venv\Scripts\python.exe" server.py
) else (
    echo [ERROR] venv not found. Run setup.bat first.
    pause
)
