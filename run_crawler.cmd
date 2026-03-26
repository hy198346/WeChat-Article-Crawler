@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo     WeChat Article Crawler
echo ==========================================
echo.

:: Check parameter
if "%~1"=="force" (
    echo [Mode] Force Push Mode
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_project.ps1" -Force
) else (
    echo [Mode] Normal Mode (push new articles only)
    echo.
    echo Tip: To force push latest articles, run: run_crawler.cmd force
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_project.ps1"
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [Error] Failed with exit code: %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [Done] Success!
pause
