@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Action diagnose
set "RC=%ERRORLEVEL%"
echo.
echo Diagnostic log: .gfl2_runtime\python-discovery.log
pause
exit /b %RC%
