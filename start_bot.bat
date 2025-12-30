@echo off
cd /d "c:\Users\FASH\.gemini\antigravity\scratch\prop_firm_bot"
echo Starting Prop Firm Bot...
echo using Python: C:\Users\FASH\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe
"C:\Users\FASH\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe" main.py
if %ERRORLEVEL% NEQ 0 (
    echo Bot crashed or exited with an error.
)
pause
