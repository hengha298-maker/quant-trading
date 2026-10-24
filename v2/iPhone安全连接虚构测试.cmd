@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "GOOSE_PHONE_PY=%LOCALAPPDATA%\GooseP1SinaTools-K2-1.19.1\Scripts\python.exe"
if exist "%GOOSE_PHONE_PY%" (
  "%GOOSE_PHONE_PY%" phone_launcher.py
) else (
  py -3.12 phone_launcher.py
)
set "GOOSE_PHONE_RESULT=%ERRORLEVEL%"
popd
if not "%GOOSE_PHONE_RESULT%"=="0" pause
exit /b %GOOSE_PHONE_RESULT%
