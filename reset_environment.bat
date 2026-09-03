@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo [GFL2 Tools] Reset project-local runtime
echo This removes ONLY .gfl2_runtime.
echo The data folder, database, snapshots and imported data are NOT deleted.
echo.
set /p CONFIRM=Type RESET to continue: 
if /I not "%CONFIRM%"=="RESET" exit /b 0

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Action reset
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" echo Runtime reset complete.
if not "%RC%"=="0" echo Reset failed. Close all GFL2 Tools windows and retry.
pause
exit /b %RC%
