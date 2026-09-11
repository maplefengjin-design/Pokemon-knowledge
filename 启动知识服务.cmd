@echo off
setlocal
chcp 65001 >nul
title Pokemon Knowledge API

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"

python tools\run_api.py --host 127.0.0.1 --port 8766
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Knowledge API exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
