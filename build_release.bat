@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "BUILD_ENV=.gfl2_build"
set "PY=%BUILD_ENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo [GFL2 Tools] Creating isolated release build environment...
    python -m venv "%BUILD_ENV%"
    if errorlevel 1 goto :fail
)

echo [GFL2 Tools] Preparing build dependencies...
"%PY%" -m pip install --disable-pip-version-check -U pip >nul
if errorlevel 1 goto :fail
"%PY%" -m pip install --disable-pip-version-check -e ".[build]"
if errorlevel 1 goto :fail

echo.
echo [GFL2 Tools] Building Windows executable release...
"%PY%" tools\build_executable.py --output release
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :fail_code

echo.
echo [GFL2 Tools] Release build complete.
for /f "delims=" %%V in ('"%PY%" -c "from gfl2tool import __version__; print(__version__)"') do set "VERSION=%%V"
echo [GFL2 Tools] Run: release\GF2Tools-v%VERSION%-win64\GF2Tools.exe
for %%F in (release\GF2Tools-v*-win64.zip) do echo [GFL2 Tools] GitHub Release asset: %%F
echo [GFL2 Tools] GPLv3 corresponding source: release\source\
exit /b 0

:fail
set "RC=%ERRORLEVEL%"
:fail_code
echo.
echo [GFL2 Tools] Release build failed with code %RC%.
pause
exit /b %RC%
