@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "GOOSE_TEST_PY=%LOCALAPPDATA%\GooseP1SinaTools-K2-1.19.1\Scripts\python.exe"
if exist "%GOOSE_TEST_PY%" (
  "%GOOSE_TEST_PY%" readonly_launcher.py
) else (
  py -3.12 readonly_launcher.py
)
set "GOOSE_TEST_RESULT=%ERRORLEVEL%"
popd
if not "%GOOSE_TEST_RESULT%"=="0" pause
exit /b %GOOSE_TEST_RESULT%
