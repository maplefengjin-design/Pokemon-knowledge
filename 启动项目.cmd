@echo off
setlocal
chcp 65001 >nul
title Pokemon Knowledge

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.12+ and add it to PATH.
    echo.
    pause
    exit /b 1
)

python tools\interactive_console.py
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] The launcher exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
