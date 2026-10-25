@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found. Install Python 3.10+ first.
  pause
  exit /b 1
)

python -c "import flask, requests, bs4, lxml" >nul 2>nul
if errorlevel 1 (
  echo [SETUP] Installing dependencies...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
  )
)

echo.
echo   Spider Web Crawler
echo   http://127.0.0.1:5000
echo.
python app.py --open
if errorlevel 1 pause
