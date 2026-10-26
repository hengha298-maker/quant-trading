@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0..\Runtime\Python\Scripts\python.exe" (
    "%~dp0..\Runtime\Python\Scripts\python.exe" -I -B "%~dp0v3\launch.py"
) else (
    py -3.12 -I -B "%~dp0v3\launch.py"
)
if errorlevel 1 (
    echo Goose V3 did not start. Keep the stable V2 entry unchanged.
    pause
)
endlocal
