@echo off
echo ============================================
echo   ReplayKit - Setup
echo ============================================
echo.

cd /d "%~dp0"

:: Detect production mode (frontend/dist exists, no package.json)
set "PRODUCTION=0"
if exist "frontend\dist\index.html" (
    if not exist "frontend\package.json" set "PRODUCTION=1"
)

:: Create Python venv
echo [1/5] Creating Python venv...
if not exist "venv" (
    py -3.10 -m venv venv
    echo       venv created
) else (
    echo       venv already exists - skipped
)

:: Install pip packages
echo [2/5] Installing Python packages...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
if exist "lge.auto-*.whl" (
    for %%f in (lge.auto-*.whl) do pip install "%%f"
    echo       lge.auto installed
) else (
    echo       [Note] lge.auto .whl not found
)

:: Node.js (dev mode only)
if "%PRODUCTION%"=="1" (
    echo [3/5] Production mode - skipping Node.js
    goto :skip_npm
)

echo [3/5] Checking Node.js...
where npm.cmd >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo       Node.js is not installed.
    if exist "node-*.msi" (
        echo       Installing bundled Node.js MSI...
        for %%f in (node-*.msi) do msiexec /i "%%f" /passive /norestart
        echo       Node.js installed
        echo       [IMPORTANT] Close this window and re-run setup.bat for PATH to take effect.
        pause
        exit /b 0
    )
    echo       Please install Node.js LTS from https://nodejs.org
    goto :skip_npm
) else (
    for /f "tokens=*" %%v in ('node --version 2^>nul') do echo       Node.js %%v detected
)

echo [4/5] Installing frontend packages...
cd frontend
call npm install
cd ..
echo       npm install done

:skip_npm

:: Build ReplayKit.exe (production mode only)
if "%PRODUCTION%"=="1" (
    if not exist "ReplayKit.exe" (
        echo [5/5] Building ReplayKit.exe...
        pip install pyinstaller -q
        pyinstaller --onefile --noconsole --name ReplayKit server.py --distpath . --workpath build\pyinstaller --specpath build 2>nul
        if exist "ReplayKit.exe" (
            echo       ReplayKit.exe build complete
        ) else (
            echo       [Warning] exe build failed - use: python server.py
        )
        if exist "build" rd /s /q build
        if exist "ReplayKit.spec" del ReplayKit.spec
    ) else (
        echo [5/5] ReplayKit.exe already exists - skipped
    )
) else (
    echo [5/5] Dev mode - skipping exe build
)

call deactivate

echo.
echo ============================================
echo   Setup complete!
if "%PRODUCTION%"=="1" (
    echo   Run ReplayKit.exe to start.
) else (
    echo   Run: python server.py
)
echo ============================================
pause
