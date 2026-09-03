@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo [GFL2 Tools] Starting bootstrap...

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"

"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Action start
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [GFL2 Tools] Startup failed with code %RC%.
    echo [GFL2 Tools] See .gfl2_runtime\python-discovery.log and bootstrap.log for details.
    pause
)
exit /b %RC%
