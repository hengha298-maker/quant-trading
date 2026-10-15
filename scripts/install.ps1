$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '请安装 Python 3.12（python.org），勾选 Python Launcher。' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
& .\.venv\Scripts\python.exe -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw '程序安装失败' }
if (!(Test-Path config.json)) { Copy-Item config.example.json config.json }
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw '测试失败，请勿配置自动任务' }
Write-Host '安装与测试完成。运行：powershell -File .\scripts\run.ps1'
