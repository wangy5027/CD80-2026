@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
    echo Windows Python environment is missing. See LOCAL-WINDOWS.md.
    pause
    exit /b 1
)
echo Website: http://127.0.0.1:8894/desktop/
echo Data API: http://127.0.0.1:8897/docs
echo Keep this window open. Press Ctrl+C to stop both services.
".venv\Scripts\python.exe" "cd80-data-api\scripts\start_all.py" %*
if errorlevel 1 pause
