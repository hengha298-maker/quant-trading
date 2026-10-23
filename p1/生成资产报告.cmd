@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "P1_REPORT_PY=%LOCALAPPDATA%\GooseP1SinaTools-K2-1.19.1\Scripts\python.exe"
if exist "%P1_REPORT_PY%" (
  "%P1_REPORT_PY%" -m goose_research.report_wizard
) else (
  py -3.12 -m goose_research.report_wizard
)
set "P1_REPORT_RESULT=%ERRORLEVEL%"
popd
if not "%P1_REPORT_RESULT%"=="0" pause
exit /b %P1_REPORT_RESULT%
