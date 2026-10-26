@echo off
setlocal
cd /d "%~dp0"
"%~dp0..\Runtime\Research32\Scripts\python.exe" -I -B "%~dp0v32\run.py" sample
if errorlevel 1 echo Research failed. Existing V3.1 and V1 remain unchanged.
pause
endlocal
