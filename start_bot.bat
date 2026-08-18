@echo off
cd /d "%~dp0"
echo ==========================================
echo   Prop Firm Signal Bot - Signal Only Mode
echo   No MetaTrader5 required
echo ==========================================
echo.
echo Checking Python...
python --version
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b
)

echo.
echo Installing/checking dependencies...
python -m pip install -r requirements.txt --quiet

echo.
echo Starting Signal Bot...
echo (Press Ctrl+C to stop)
echo.
python main.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Bot stopped with an error.
)
pause
