@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: 임베디드 Python 격리 — 시스템 Python의 환경변수가 임베디드 Python에
:: 영향을 주지 못하도록 차단 (cv2 DLL load 충돌 방지)
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONSTARTUP="
set "PYTHONNOUSERSITE=1"

:: Git PATH 확보
set "PATH=C:\Program Files\Git\cmd;C:\Program Files (x86)\Git\cmd;%PATH%"

REM Stop existing server BEFORE git pull / pip install — required to release .pyd locks
call :stop_existing_server

:: --home 옵션: git_remote_home.txt 사용
set "GIT_REMOTE_FILE=git_remote.txt"
if "%~1"=="--home" (
    if exist "git_remote_home.txt" (
        set "GIT_REMOTE_FILE=git_remote_home.txt"
        echo [GIT] Using home remote: git_remote_home.txt
    ) else (
        echo [GIT] git_remote_home.txt not found - using default.
    )
)

:: 정식 remote URL — 이 주소가 아니면 자동 교정
set "CANONICAL_REMOTE=http://mod.lge.com/hub/dqa_replay_kit/replay_kit.git"

:: Git 초기화 또는 업데이트
if not exist ".git" (
    if exist "%GIT_REMOTE_FILE%" (
        where git.exe >nul 2>nul
        if not errorlevel 1 (
            call :git_init
        ) else (
            echo [GIT] Git not found - skipping.
        )
    )
) else (
    where git.exe >nul 2>nul
    if not errorlevel 1 (
        if /i not "%~1"=="--home" call :fix_remote
        call :git_pull
    )
)
goto :after_git

:git_init
echo [GIT] Initializing repository...
set /p GIT_REMOTE=<%GIT_REMOTE_FILE%
set "SAFE_DIR=%CD:\=/%"
git init -b main
git config --global --add safe.directory "%SAFE_DIR%"
git remote add origin "%GIT_REMOTE%"
git fetch --depth 1 origin main
if errorlevel 1 (
    echo [GIT] Fetch failed - check network.
    goto :eof
)
git branch --set-upstream-to=origin/main main
git reset origin/main
git checkout origin/main -- .gitignore
echo [GIT] Initialized: %GIT_REMOTE%
goto :eof

:fix_remote
:: 현재 origin URL이 정식 주소가 아니면 교정
set "SAFE_DIR=%CD:\=/%"
for /f "delims=" %%u in ('git -c safe.directory="%SAFE_DIR%" remote get-url origin 2^>nul') do set "CUR_REMOTE=%%u"
if not "!CUR_REMOTE!"=="!CANONICAL_REMOTE!" (
    git -c safe.directory="%SAFE_DIR%" remote set-url origin "!CANONICAL_REMOTE!"
    echo [GIT] Remote corrected: !CUR_REMOTE! -^> !CANONICAL_REMOTE!
)
goto :eof

:git_pull
:: --home 옵션일 때는 origin URL을 건드리지 않고 현재 설정 그대로 fetch+reset
set "SAFE_DIR=%CD:\=/%"
git -c safe.directory="%SAFE_DIR%" fetch origin main
git -c safe.directory="%SAFE_DIR%" reset --hard origin/main
echo [GIT] Updated.
goto :eof

:after_git

:: Auto dependency update - pip install only when requirements.txt changed
:: Uses .req_hash pattern same as build_dist.py (python\.req_hash)
if exist "python\python.exe" if exist "requirements.txt" call :update_deps
goto :start_server

REM ────────────────────────────────────────────────────────────
REM  stop_existing_server: kill running backend / frontend / python
REM ────────────────────────────────────────────────────────────
:stop_existing_server
echo [STOP] Checking for running server...
REM 1) Kill backend (port 8000)
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr /r /c:":8000 .*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    if not errorlevel 1 echo [STOP] Killed backend PID %%p ^(port 8000^)
)
REM 2) Kill frontend Vite dev server (port 5173)
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr /r /c:":5173 .*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    if not errorlevel 1 echo [STOP] Killed frontend PID %%p ^(port 5173^)
)
REM 3) Kill python(w).exe running server.py / _launcher.py (covers tray/GUI mode)
where powershell.exe >nul 2>&1
if not errorlevel 1 (
    powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'server\.py|_launcher\.py' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[STOP] Killed Python PID ' + $_.ProcessId) } catch {} }" 2>nul
)
REM 4) Wait for port release (TIME_WAIT)
timeout /t 1 /nobreak >nul 2>&1
goto :eof

:update_deps
set "REQ_HASH_FILE=python\.req_hash"
set "OLD_HASH="
if exist "%REQ_HASH_FILE%" set /p OLD_HASH=<"%REQ_HASH_FILE%"
set "NEW_HASH="
for /f "skip=1 tokens=1" %%h in ('certutil -hashfile "requirements.txt" SHA256 2^>nul') do (
    if not defined NEW_HASH set "NEW_HASH=%%h"
)
if not defined NEW_HASH goto :eof

:: 핵심 모듈 import 가능 여부 — requirements.txt 미동기화 대비 안전장치.
:: 누락이면 해시가 같아도 강제 재설치.
set "NEED_INSTALL="
if /i not "!NEW_HASH!"=="!OLD_HASH!" set "NEED_INSTALL=1"
python\python.exe -c "import rapidocr_onnxruntime, rapidfuzz" >nul 2>nul
if errorlevel 1 (
    set "NEED_INSTALL=1"
    set "CRITICAL_MISSING=1"
)
if not defined NEED_INSTALL goto :eof

echo [DEPS] Installing/updating packages...
python\python.exe -E -s -m pip install -r requirements.txt --no-warn-script-location -q
if errorlevel 1 (
    echo [DEPS] Install failed - continuing with existing packages.
    goto :eof
)

:: requirements.txt 동기화 누락 시 핵심 모듈 직접 설치 (requirements 에 없을 수도 있음)
if defined CRITICAL_MISSING (
    python\python.exe -c "import rapidocr_onnxruntime, rapidfuzz" >nul 2>nul
    if !errorlevel! NEQ 0 (
        echo [DEPS] Critical modules still missing - installing directly...
        python\python.exe -E -s -m pip install rapidocr-onnxruntime rapidfuzz --no-warn-script-location -q
    )
)

>"%REQ_HASH_FILE%" echo !NEW_HASH!
echo [DEPS] Dependencies updated.
goto :eof

:start_server
set "ENTRY=server.py"
if exist "_launcher.py" set "ENTRY=_launcher.py"

set "PY="
set "PYW="
if exist "python\pythonw.exe" set "PYW=python\pythonw.exe"
if exist "python\python.exe" set "PY=python\python.exe"
if not defined PYW if exist "venv\Scripts\pythonw.exe" set "PYW=venv\Scripts\pythonw.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"

if not defined PYW if not defined PY (
    echo [ERROR] Python not found. Run setup.bat first.
    pause
    exit /b 1
)

if defined PYW (
    echo [START] %PYW% %ENTRY%
    start "" "%PYW%" %ENTRY%
) else (
    echo [START] %PY% %ENTRY%
    start "" cmd /c ""%PY%" %ENTRY% || (echo. & echo [ERROR] Server crashed. Press any key to close. & pause >nul)"
)
