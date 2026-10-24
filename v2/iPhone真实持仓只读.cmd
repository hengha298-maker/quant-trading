@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "GOOSE_PRIVATE_PY=%LOCALAPPDATA%\GooseP1SinaTools-K2-1.19.1\Scripts\python.exe"
if exist "%GOOSE_PRIVATE_PY%" (
  "%GOOSE_PRIVATE_PY%" private_launcher.py
) else (
  py -3.12 private_launcher.py
)
set "GOOSE_PRIVATE_RESULT=%ERRORLEVEL%"
popd
if not "%GOOSE_PRIVATE_RESULT%"=="0" pause
exit /b %GOOSE_PRIVATE_RESULT%
