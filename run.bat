@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Python environment not found. Run install.bat first.
    pause
    exit /b 1
)

if /I not "%~1"=="--elevated" (
    net session >nul 2>&1
    if errorlevel 1 (
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
          "Start-Process -FilePath $env:ComSpec -ArgumentList '/c','\"\"%~f0\" --elevated\"' -WorkingDirectory '%~dp0' -Verb RunAs"
        exit /b
    )
)

start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
