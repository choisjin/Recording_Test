@echo off
cd /d "%~dp0"

:: Git이 있고 .git 저장소면 시작 전 최신화
if exist ".git" (
    where git.exe >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        echo [UPDATE] Pulling latest changes...
        git pull origin main --ff-only 2>nul
        if %ERRORLEVEL% equ 0 (
            echo [UPDATE] Up to date.
        ) else (
            echo [UPDATE] Pull failed - starting with current version.
        )
    )
)

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
