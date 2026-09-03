@echo off
setlocal
cd /d "%~dp0"

rem Prefer the venv. It has PySide6, and 24 of the tests skip without it,
rem so the system interpreter runs 55 of 79 and calls that a pass.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m unittest discover -s tests -v
) else (
    echo .venv not found, using the system interpreter.
    echo The PySide6 tests will report as skipped; run install.bat to include them.
    echo.
    py -3 -m unittest discover -s tests -v
)
pause
