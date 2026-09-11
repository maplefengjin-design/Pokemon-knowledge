@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist "config\llm.env" (
    copy /Y "config\llm.env.example" "config\llm.env" >nul
)

start "" notepad.exe "%~dp0config\llm.env"
exit /b 0
