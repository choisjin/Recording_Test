@echo off
chcp 65001 >nul
echo ============================================
echo   Recording Test - 초기 환경 설정
echo ============================================
echo.

cd /d "%~dp0"

:: Python venv 생성
echo [1/4] Python 가상환경 생성 중...
if not exist "venv" (
    py -3.10 -m venv venv
    echo       venv 생성 완료
) else (
    echo       venv 이미 존재함 - 건너뜀
)

:: pip 패키지 설치
echo [2/4] Python 패키지 설치 중...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if exist "lge.auto-*.whl" (
    for %%f in (lge.auto-*.whl) do pip install "%%f"
    echo       lge.auto 설치 완료
) else (
    echo       [주의] lge.auto .whl 파일이 없습니다. 수동으로 복사해주세요.
)
call deactivate

:: Node 패키지 설치
echo [3/4] Frontend 패키지 설치 중...
cd frontend
call npm install
cd ..

echo.
echo [4/4] 설정 완료!
echo ============================================
echo   수동 복사 필요 파일 (Git 미포함):
echo     - lge.auto-*.whl  (없으면 위 경고 참고)
echo     - CANatTransportProcDll.dll
echo     - server.exe
echo ============================================
pause
