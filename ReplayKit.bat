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

:: 배포 환경: _launcher.py, 개발 환경: server.py
set "ENTRY=server.py"
if exist "_launcher.py" set "ENTRY=_launcher.py"

if exist "python\pythonw.exe" (
    start "" "python\pythonw.exe" %ENTRY%
) else if exist "python\python.exe" (
    start "" "python\python.exe" %ENTRY%
) else if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" %ENTRY%
) else if exist "venv\Scripts\python.exe" (
    start "" "venv\Scripts\python.exe" %ENTRY%
) else (
    echo [ERROR] Python not found. Run setup.bat first.
    pause
)
