@echo off
chcp 65001 >nul
echo ============================================
echo   Recording Test - 동기화 및 실행
echo ============================================
echo.

cd /d "%~dp0"

:: Git pull
echo [1/3] 최신 코드 가져오는 중...
git pull origin main
if errorlevel 1 (
    echo       [오류] git pull 실패. 충돌을 확인해주세요.
    pause
    exit /b 1
)
echo.

:: 의존성 업데이트 (변경 시에만)
echo [2/3] 의존성 확인 중...
call venv\Scripts\activate.bat
pip install -r requirements.txt -q
cd frontend
call npm install --silent
cd ..
echo       의존성 업데이트 완료
echo.

:: 서버 시작
echo [3/3] 서버 시작 중...
echo       백엔드:    http://localhost:8000
echo       프론트엔드: http://localhost:5173
echo.
echo   종료하려면 이 창을 닫으세요.
echo ============================================
echo.

:: 백엔드 시작 (백그라운드)
start "Recording Test - Backend" cmd /c "call venv\Scripts\activate.bat && python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload"

:: 프론트엔드 시작 (백그라운드)
start "Recording Test - Frontend" cmd /c "cd frontend && npm run dev"

:: 브라우저 열기 (3초 대기 후)
timeout /t 3 /nobreak >nul
start http://localhost:5173
