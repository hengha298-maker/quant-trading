@echo off
setlocal
cd /d "%~dp0"
"%~dp0..\Runtime\Research32\Scripts\python.exe" -I -B "%~dp0v33\run.py" check
echo.
echo Exit 2 means intraday health is NOT verified. No Bark or trades were sent.
echo Read the new v33\work run folder for report.md and health.json.
pause
endlocal
