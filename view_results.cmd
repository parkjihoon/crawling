@echo off
echo ========================================
echo  Crawling Dashboard Viewer
echo ========================================
echo.

cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    echo Starting dashboard server...
    echo Open http://localhost:8000 in your browser
    echo Press Ctrl+C to stop
    echo.
    venv\Scripts\python.exe viewer.py %*
) else (
    echo ERROR: Virtual environment not found.
    echo Run setup_and_run.cmd first.
)

pause
