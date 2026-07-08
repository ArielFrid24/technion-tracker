@echo off
title Technion Tracker - Launcher
cd /d "%~dp0"

echo ============================================
echo   TECHNION TRACKER
echo ============================================
echo.

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

timeout /t 2 /nobreak >nul

echo Starting app - your browser will open automatically...
echo.
echo To stop the app later, just close the two windows this opened.
echo.
pushd ui
start "Technion Tracker - App (keep this window open)" /min cmd /k "npm start"
popd
