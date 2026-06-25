# install_cron.ps1 — register the QUANTDATA daily refresh as a Windows
# Scheduled Task (the Windows equivalent of install_cron.sh's crontab entry).
# Linux / macOS / WSL2: use install_cron.sh (crontab).
#
# Runs scripts\daily_refresh.ps1 every weekday at 17:30 local time
# (台股 13:30 收盤 + 4h 給 TEJ EOD 落地).
#
# Usage:
#   .\scripts\install_cron.ps1                 # install / replace task
#   .\scripts\install_cron.ps1 -Uninstall      # remove task
#   .\scripts\install_cron.ps1 -Show           # print what would be installed
#   .\scripts\install_cron.ps1 -Hour 17 -Minute 30   # override schedule
#
# Idempotent: re-running replaces the task of the same name only.

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Show,
    [int]$Hour   = 17,
    [int]$Minute = 30
)

$ErrorActionPreference = 'Stop'
$Repo      = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Refresh   = Join-Path $Repo 'scripts\daily_refresh.ps1'
$TaskName  = 'QUANTDATA-daily-refresh'

if ($Show) {
    Write-Host "Task name : $TaskName"
    Write-Host "Action    : pwsh -NoProfile -File `"$Refresh`""
    Write-Host "Schedule  : Weekly Mon-Fri at $('{0:D2}:{1:D2}' -f $Hour,$Minute) local time"
    Write-Host "Logs      : $Repo\meta\audit\daily_refresh_YYYY-MM-DD.log"
    exit 0
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[uninstall] removed scheduled task '$TaskName'"
    } else {
        Write-Host "[uninstall] task '$TaskName' not found (nothing to do)"
    }
    exit 0
}

if (-not (Test-Path $Refresh)) {
    Write-Host "ERROR: $Refresh not found" -ForegroundColor Red
    exit 1
}

# Prefer pwsh 7 if present, else Windows PowerShell.
$psExe = if (Get-Command pwsh.exe -ErrorAction SilentlyContinue) { 'pwsh.exe' } else { 'powershell.exe' }

$action  = New-ScheduledTaskAction -Execute $psExe `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Refresh`"" `
            -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
            -At ([datetime]::Today.AddHours($Hour).AddMinutes($Minute))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd

# Replace if exists (idempotent).
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'QUANTDATA daily TEJ refresh + ingest + catalog rebuild' | Out-Null

Write-Host "[install] scheduled task '$TaskName' registered"
Write-Host "Schedule : Mon-Fri $('{0:D2}:{1:D2}' -f $Hour,$Minute) local time"
Write-Host "Uninstall: .\scripts\install_cron.ps1 -Uninstall"
