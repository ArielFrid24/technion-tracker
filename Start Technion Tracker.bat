@echo off
title Technion Tracker - Launcher
cd /d "%~dp0"

echo ============================================
echo   TECHNION TRACKER
echo ============================================
echo.

:: Clean up any leftover backend/frontend from a previous run that wasn't
:: stopped properly, so starting fresh never fails with "port already in use"
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5000 " ^| findstr LISTENING') do (
    taskkill /F /PID %%p /T >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3000 " ^| findstr LISTENING') do (
    taskkill /F /PID %%p /T >nul 2>&1
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on this computer.
    echo Please install it from https://python.org and try again.
    echo.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js was not found on this computer.
    echo Please install it from https://nodejs.org and try again.
    echo.
    pause
    exit /b 1
)

python -c "import flask, flask_cors" >nul 2>nul
if errorlevel 1 (
    echo Installing backend dependencies - one-time setup, this may take a minute...
    python -m pip install flask flask-cors
    echo.
)

if not exist "ui\node_modules" (
    echo Installing frontend dependencies - one-time setup, this may take a few minutes...
    pushd ui
    call npm install
    popd
    echo.
)

echo Starting backend...
start "Technion Tracker - Backend (keep this window open)" /min cmd /k "python app.py"

ping -n 3 127.0.0.1 >nul

echo Starting app - your browser will open automatically...
echo.
echo To stop the app later, double-click "Stop Technion Tracker.bat"
echo (or just leave it running - next time you start, leftovers are cleaned up automatically).
echo.
pushd ui
start "Technion Tracker - App (keep this window open)" /min cmd /k "npm start"
popd
