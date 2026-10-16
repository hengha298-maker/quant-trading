param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$name = 'QuantTrading-Paper-V1'
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    exit
}
# Windows task triggers use local time. Refuse other zones rather than silently trade at wrong times.
if ((Get-TimeZone).Id -ne 'China Standard Time') {
    throw '请先在 Windows 设置中把时区设置为 (UTC+08:00) 北京，再安装任务。'
}
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { throw '请先执行 install.ps1' }
$action = New-ScheduledTaskAction -Execute $python -Argument '-m quant_paper.cli run --notify always' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '14:50'
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
# No password, no elevated privileges; user must be logged in and PC awake.
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description '纯模拟交易；北京时间14:50，节假日由程序跳过' -Force
