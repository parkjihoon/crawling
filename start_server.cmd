@echo off
echo ========================================
echo  Crawling Dashboard Server
echo ========================================
echo.

cd /d "%~dp0"

if not exist "venv" (
    echo Creating virtual environment...
    py -3 -m venv venv
)

REM 모든 requirements를 설치한다. (이전 버전은 flask/scrapling/patchright만 설치해
REM StealthyFetcher가 런타임에 curl_cffi 등을 import하지 못하고 실패하는 이슈가 있었음)
echo Installing dependencies...
venv\Scripts\python.exe -m pip install -r requirements.txt --quiet

REM patchright 번들 chromium이 필요한 경우 (real_chrome=False 경로) 설치
REM 시스템 Chrome을 쓰는 경우(기본 auto-detect)는 엄격히 필수는 아니지만,
REM 설치 상태를 일관되게 유지하기 위해 체크한다.
if not exist "%LOCALAPPDATA%\ms-playwright\chromium-1208" (
    echo Installing patchright chromium...
    venv\Scripts\python.exe -m patchright install chromium
)

echo.
echo Starting server at http://localhost:5000
echo Press Ctrl+C to stop
echo.

venv\Scripts\python.exe server.py %*
