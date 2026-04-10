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

echo Installing dependencies...
venv\Scripts\pip.exe install flask scrapling patchright --quiet 2>nul

echo.
echo Starting server at http://localhost:5000
echo Press Ctrl+C to stop
echo.

venv\Scripts\python.exe server.py %*
