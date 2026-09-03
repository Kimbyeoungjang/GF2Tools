@echo off
setlocal
cd /d "%~dp0"
echo [GFL2 Tools] Repairing OCR engine and Korean/English language data...
py -3 bootstrap.py --repair-ocr
if errorlevel 1 (
  python bootstrap.py --repair-ocr
)
pause
