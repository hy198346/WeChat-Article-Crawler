@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_project.ps1" -PauseOnError -PauseOnFinish
echo.
echo Log files are under .\logs\
pause

