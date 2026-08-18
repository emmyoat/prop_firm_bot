@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo   Prop Firm Signal Bot - Signal Only Mode
echo   No MetaTrader5 required
echo ==========================================
echo.

echo Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 2
)

echo.
echo Installing/checking dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: Runtime dependency installation failed.
    pause
    exit /b 3
)

set "RESTART_DELAY=5"
set "MAX_RESTART_DELAY=300"

:run
 echo.
 echo Starting Signal Bot...
 echo Press Ctrl+C once to request a clean stop.
 echo.
 python main.py %*
 set "EXIT_CODE=!ERRORLEVEL!"

 if "!EXIT_CODE!"=="0" (
     echo Bot stopped cleanly. No restart requested.
     exit /b 0
 )

 echo.
 echo Bot exited with code !EXIT_CODE!.
 echo Restarting in !RESTART_DELAY! seconds. Press Ctrl+C to abort.
 timeout /t !RESTART_DELAY! /nobreak >nul
 set /a NEXT_DELAY=RESTART_DELAY*2
 if !NEXT_DELAY! GTR !MAX_RESTART_DELAY! set "NEXT_DELAY=!MAX_RESTART_DELAY!"
 set "RESTART_DELAY=!NEXT_DELAY!"
 goto run
