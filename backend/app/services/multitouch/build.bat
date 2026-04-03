@echo off
REM 멀티터치 DEX 빌드 스크립트
REM 요구사항: Android SDK (android.jar + d8)
REM
REM 사용법: build.bat [android-sdk-path]
REM 예: build.bat C:\Users\me\AppData\Local\Android\Sdk

setlocal

set SDK=%1
if "%SDK%"=="" (
    if defined ANDROID_HOME set SDK=%ANDROID_HOME%
    if defined ANDROID_SDK_ROOT set SDK=%ANDROID_SDK_ROOT%
    if "%SDK%"=="" set SDK=%LOCALAPPDATA%\Android\Sdk
)

REM android.jar 찾기
set ANDROID_JAR=
for /d %%d in ("%SDK%\platforms\android-*") do set ANDROID_JAR=%%d\android.jar
if not exist "%ANDROID_JAR%" (
    echo ERROR: android.jar not found in %SDK%\platforms\
    echo Install Android SDK Platform via SDK Manager
    exit /b 1
)

REM d8 찾기
set D8=
for /d %%d in ("%SDK%\build-tools\*") do set D8=%%d\d8.bat
if not exist "%D8%" (
    echo ERROR: d8 not found in %SDK%\build-tools\
    exit /b 1
)

echo Using android.jar: %ANDROID_JAR%
echo Using d8: %D8%

REM 컴파일
javac -source 1.8 -target 1.8 -cp "%ANDROID_JAR%" Multitouch.java
if errorlevel 1 (
    echo ERROR: javac failed
    exit /b 1
)

REM DEX 변환
call "%D8%" --min-api 21 --output . Multitouch.class
if errorlevel 1 (
    echo ERROR: d8 failed
    exit /b 1
)

REM 이름 변경
if exist classes.dex (
    move /y classes.dex multitouch.dex >nul
    echo SUCCESS: multitouch.dex created
) else (
    echo ERROR: classes.dex not generated
    exit /b 1
)

REM 정리
del /q Multitouch.class 2>nul

endlocal
