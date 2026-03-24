@echo off
cd /d "%~dp0"
if exist "python\pythonw.exe" (
    start "" "python\pythonw.exe" server.py
) else if exist "python\python.exe" (
    start "" "python\python.exe" server.py
) else if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" server.py
) else if exist "venv\Scripts\python.exe" (
    start "" "venv\Scripts\python.exe" server.py
) else (
    echo [ERROR] Python not found. Run setup.bat first.
    pause
)
