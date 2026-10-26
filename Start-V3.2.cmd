@echo off
setlocal
cd /d "%~dp0"
"%~dp0..\Runtime\Research32\Scripts\python.exe" -I -B "%~dp0v3\launch.py"
if errorlevel 1 (
    echo V3.2 did not start. V3.1 remains on its separate port 9393.
    pause
)
endlocal
