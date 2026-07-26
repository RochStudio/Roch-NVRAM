@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe app.py --offline
) else (
    echo Python environment not found. Run install.bat first.
    pause
)
