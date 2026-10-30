@echo off
setlocal
cd /d "%~dp0"
"%~dp0..\Runtime\Research32\Scripts\python.exe" -I -B "%~dp0investment\run.py"
if errorlevel 1 pause
endlocal
