@echo off
echo ============================================
echo   ReplayKit - Setup
echo ============================================
echo.

cd /d "%~dp0"

:: -------------------------------------------------------
:: Refresh PATH from registry
:: -------------------------------------------------------
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USR_PATH=%%b"
if defined SYS_PATH set "PATH=%SYS_PATH%"
if defined USR_PATH set "PATH=%PATH%;%USR_PATH%"
if exist "C:\Python310" set "PATH=C:\Python310;C:\Python310\Scripts;%PATH%"

:: Detect production mode
set "PRODUCTION=0"
if exist "frontend\dist\index.html" (
    if not exist "frontend\package.json" set "PRODUCTION=1"
)

:: -------------------------------------------------------
:: [1/5] Check Python 3.10
:: -------------------------------------------------------
echo [1/5] Checking Python 3.10...

set "PYTHON="
py -3.10 --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON=py -3.10"
    goto :python_ok
)
if exist "C:\Python310\python.exe" (
    C:\Python310\python.exe --version >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "PYTHON=C:\Python310\python.exe"
        goto :python_ok
    )
)

:: Python not found - launch bundled installer
echo.
echo       Python 3.10 is not installed.
if exist "python-3.10.4-amd64.exe" (
    echo.
    echo       ================================================
    echo       Python 3.10 installer will now open.
    echo       [TIP] Check "Add Python to PATH" at the bottom!
    echo       ================================================
    echo.
    start "" /wait "python-3.10.4-amd64.exe"
    echo.
    echo       ------------------------------------------
    echo       Installation complete. Press any key to continue...
    echo       ------------------------------------------
    pause >nul

    :: Refresh PATH after install
    for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
    for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USR_PATH=%%b"
    if defined SYS_PATH set "PATH=%SYS_PATH%"
    if defined USR_PATH set "PATH=%PATH%;%USR_PATH%"
    if exist "C:\Python310" set "PATH=C:\Python310;C:\Python310\Scripts;%PATH%"

    :: Re-check
    set "PYTHON="
    py -3.10 --version >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "PYTHON=py -3.10"
        goto :python_ok
    )
    if exist "C:\Python310\python.exe" (
        set "PYTHON=C:\Python310\python.exe"
        goto :python_ok
    )
    python --version >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set "PYTHON=python"
        goto :python_ok
    )

    echo       [ERROR] Python still not detected. Please restart this script.
    pause
    exit /b 1
) else (
    echo       [ERROR] python-3.10.4-amd64.exe not found.
    echo       Please install Python 3.10 from https://www.python.org/downloads/
    pause
    exit /b 1
)

:python_ok
for /f "tokens=*" %%v in ('%PYTHON% --version 2^>nul') do echo       %%v detected

:: -------------------------------------------------------
:: [2/5] Create Python venv
:: -------------------------------------------------------
echo [2/5] Creating Python venv...
if not exist "venv" (
    %PYTHON% -m venv venv
    if not exist "venv\Scripts\python.exe" (
        echo       [ERROR] venv creation failed
        pause
        exit /b 1
    )
    echo       venv created
) else (
    echo       venv already exists - skipped
)

:: -------------------------------------------------------
:: [3/5] Install pip packages
:: -------------------------------------------------------
echo [3/5] Installing Python packages...
set "VENV_PYTHON=venv\Scripts\python.exe"
set "VENV_PIP=venv\Scripts\pip.exe"

%VENV_PYTHON% -m pip install --upgrade pip -q
%VENV_PIP% install -r requirements.txt -q
if exist "lge.auto-*.whl" (
    for %%f in (lge.auto-*.whl) do %VENV_PIP% install "%%f"
    echo       lge.auto installed
) else (
    echo       [Note] lge.auto .whl not found
)

:: -------------------------------------------------------
:: [4/5] Node.js (dev mode only)
:: -------------------------------------------------------
if "%PRODUCTION%"=="1" (
    echo [4/5] Production mode - skipping Node.js
    goto :skip_npm
)

echo [4/5] Checking Node.js...
where npm.cmd >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo       Node.js is not installed.
    if exist "node-v24.14.0-x64.msi" (
        echo.
        echo       ================================================
        echo       Node.js installer will now open.
        echo       ================================================
        echo.
        start "" /wait msiexec /i "node-v24.14.0-x64.msi"
        echo.
        echo       ------------------------------------------
        echo       Installation complete. Press any key to continue...
        echo       ------------------------------------------
        pause >nul

        :: Refresh PATH
        for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
        for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USR_PATH=%%b"
        if defined SYS_PATH set "PATH=%SYS_PATH%"
        if defined USR_PATH set "PATH=%PATH%;%USR_PATH%"

        where npm.cmd >nul 2>&1
        if %ERRORLEVEL% neq 0 (
            echo       [Warning] Node.js not detected - frontend install skipped.
            goto :skip_npm
        )
    ) else (
        echo       Please install Node.js LTS from https://nodejs.org
        goto :skip_npm
    )
)

for /f "tokens=*" %%v in ('node --version 2^>nul') do echo       Node.js %%v detected

if exist "frontend\package.json" (
    echo       Installing frontend packages...
    cd frontend
    call npm install
    cd ..
    echo       npm install done
) else (
    echo       [Warning] frontend/package.json not found - skipped
)

:skip_npm

:: -------------------------------------------------------
:: [5/5] Git repository setup (production only)
:: -------------------------------------------------------
if "%PRODUCTION%"=="1" (
    if not exist ".git" (
        if exist "git_remote.txt" goto :git_setup
    )
)
goto :git_done

:git_setup
echo [5/5] Setting up git repository...
set /p GIT_REMOTE=<git_remote.txt
git init
git remote add origin "%GIT_REMOTE%"
git fetch --depth 1 origin main
git reset origin/main
echo       git repository initialized
echo       remote: %GIT_REMOTE%
goto :git_done

:git_done

echo.
echo ============================================
echo   Setup complete!
if "%PRODUCTION%"=="1" (
    echo   Run ReplayKit.bat to start.
) else (
    echo   Run: python server.py
)
echo ============================================
pause
