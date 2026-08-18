@echo off
cd /d "%~dp0"
echo ---------------------------------------------------
echo Installing Prop Firm Bot Dependencies...
echo ---------------------------------------------------

python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python 3.10+ from python.org and check "Add Python to PATH" during installation.
    pause
    exit /b
)

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing requirements...
python -m pip install -r requirements-local.txt

echo ---------------------------------------------------
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Installation Complete!
    echo You can now run start_bot.bat
) else (
    echo [ERROR] Installation Failed. Check the error messages above.
)
echo ---------------------------------------------------
pause
