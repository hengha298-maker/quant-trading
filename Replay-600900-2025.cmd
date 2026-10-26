@echo off
setlocal
cd /d "%~dp0"
"%~dp0..\Runtime\Research32\Scripts\python.exe" -I -B "%~dp0v32\run.py" backtest --dataset "%~dp0v32\work\600900-tencent-2025.json" --publish
if errorlevel 1 echo Replay failed. No V1 or private financial data was changed.
pause
endlocal
