@echo off
chcp 65001 >nul
setlocal
where py >nul 2>nul
if errorlevel 1 goto python
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 goto python
py -3.12 "%~dp0preview.py"
goto done
:python
where python >nul 2>nul
if errorlevel 1 goto missing
python -c "import sys; raise SystemExit(sys.version_info < (3,12))" >nul 2>nul
if errorlevel 1 goto missing
python "%~dp0preview.py"
goto done
:missing
echo 未找到 Python 3.12 或更新版本。此预览不会自动安装或修改运行环境。
echo 如电脑已有 V1 使用的 Python，请用该 Python 打开本目录中的 preview.py。
pause
exit /b 1
:done
if errorlevel 1 pause
endlocal
