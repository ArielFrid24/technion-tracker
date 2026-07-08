@echo off
title Technion Tracker - Stopping...
echo Stopping Technion Tracker...

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5000 " ^| findstr LISTENING') do (
    taskkill /F /PID %%p /T >nul 2>&1
    set FOUND=1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3000 " ^| findstr LISTENING') do (
    taskkill /F /PID %%p /T >nul 2>&1
    set FOUND=1
)

if "%FOUND%"=="1" (
    echo Done - Technion Tracker stopped.
) else (
    echo Nothing was running.
)
ping -n 3 127.0.0.1 >nul
