# install_cron.ps1 — register QUANTDATA scheduled tasks on Windows
# (the Windows equivalent of install_cron.sh's crontab entries).
# Linux / macOS / WSL2: use install_cron.sh (crontab).
#
# 兩個任務（彼此解耦，遷移失敗不會拖垮 ingest）：
#   QUANTDATA-daily-refresh  — 每工作日 17:30 跑 daily_refresh.ps1
#                              (台股 13:30 收盤 + 4h 給 TEJ EOD 落地)
#   QUANTDATA-daily-migrate  — 每工作日 18:30 跑 migrate_daily.ps1
#                              (refresh 之後，把新資料增量鏡像到目標機；-Migrate 才裝)
#
# Usage:
#   .\scripts\install_cron.ps1                      # 只裝/更新 refresh 任務
#   .\scripts\install_cron.ps1 -Migrate             # refresh + migrate 兩個都裝
#   .\scripts\install_cron.ps1 -Migrate -MigrateOnly # 只裝 migrate 任務
#   .\scripts\install_cron.ps1 -Uninstall           # 移除兩個任務
#   .\scripts\install_cron.ps1 -Show                # 印出將安裝的內容
#   .\scripts\install_cron.ps1 -Hour 17 -Minute 30 -MigrateHour 18 -MigrateMinute 30
#
# 無人值守遷移前置：先存目標機 SMB 憑證一次（見 migrate.conf / migrate_daily.ps1）：
#   cmdkey /add:192.168.11.33 /user:DESKTOP-B2S6D9K\User /pass:<密碼>
#
# Idempotent: 重跑只會取代同名任務。

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Show,
    [switch]$Migrate,          # 一併安裝 QUANTDATA-daily-migrate
    [switch]$MigrateOnly,      # 只安裝 migrate 任務（不動 refresh）
    [int]$Hour          = 17,
    [int]$Minute        = 30,
    [int]$MigrateHour   = 18,
    [int]$MigrateMinute = 30
)

$ErrorActionPreference = 'Stop'
$Repo         = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Refresh      = Join-Path $Repo 'scripts\daily_refresh.ps1'
$MigrateScr   = Join-Path $Repo 'scripts\migrate_daily.ps1'
$TaskName     = 'QUANTDATA-daily-refresh'
$MigrateTask  = 'QUANTDATA-daily-migrate'
if ($MigrateOnly) { $Migrate = $true }

# Prefer pwsh 7 if present, else Windows PowerShell.
$psExe = if (Get-Command pwsh.exe -ErrorAction SilentlyContinue) { 'pwsh.exe' } else { 'powershell.exe' }

if ($Show) {
    Write-Host "Task 1    : $TaskName"
    Write-Host "  Action  : $psExe -NoProfile -File `"$Refresh`""
    Write-Host "  Schedule: Weekly Mon-Fri at $('{0:D2}:{1:D2}' -f $Hour,$Minute) local time"
    Write-Host "  Logs    : $Repo\meta\audit\daily_refresh_YYYY-MM-DD.log"
    Write-Host "Task 2    : $MigrateTask  (需 -Migrate)"
    Write-Host "  Action  : $psExe -NoProfile -File `"$MigrateScr`""
    Write-Host "  Schedule: Weekly Mon-Fri at $('{0:D2}:{1:D2}' -f $MigrateHour,$MigrateMinute) local time"
    Write-Host "  Logs    : $Repo\meta\audit\migrate_YYYY-MM-DD.log"
    exit 0
}

if ($Uninstall) {
    foreach ($t in @($TaskName, $MigrateTask)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Host "[uninstall] removed scheduled task '$t'"
        } else {
            Write-Host "[uninstall] task '$t' not found (nothing to do)"
        }
    }
    exit 0
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd

function Register-One([string]$Name, [string]$Script, [int]$H, [int]$M, [string]$Desc) {
    if (-not (Test-Path $Script)) { Write-Host "ERROR: $Script not found" -ForegroundColor Red; exit 1 }
    $action  = New-ScheduledTaskAction -Execute $psExe `
                -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
                -WorkingDirectory $Repo
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
                -At ([datetime]::Today.AddHours($H).AddMinutes($M))
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings `
        -Description $Desc | Out-Null
    Write-Host "[install] scheduled task '$Name' registered — Mon-Fri $('{0:D2}:{1:D2}' -f $H,$M) local time"
}

if (-not $MigrateOnly) {
    Register-One $TaskName $Refresh $Hour $Minute 'QUANTDATA daily TEJ refresh + ingest + catalog rebuild'
}
if ($Migrate) {
    Register-One $MigrateTask $MigrateScr $MigrateHour $MigrateMinute 'QUANTDATA daily incremental migrate to target host (robocopy/SMB)'
    Write-Host "[note] 無人值守遷移前，請先存目標機 SMB 憑證一次："
    Write-Host "       cmdkey /add:192.168.11.33 /user:DESKTOP-B2S6D9K\User /pass:<密碼>"
}

Write-Host "Uninstall: .\scripts\install_cron.ps1 -Uninstall"
