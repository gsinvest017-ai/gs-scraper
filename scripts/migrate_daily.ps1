# migrate_daily.ps1 — 無人值守「每日增量遷移」wrapper（Windows 排程用）。
#
# 內部呼叫既有的 scripts/migrate_to_host.sh 的 Windows/SMB 分支（robocopy over SMB），
# 目標主機讀 scripts/migrate.conf。設計成 QUANTDATA-daily-migrate 排程任務的 Action，
# 一般接在 daily_refresh 之後跑（先有新資料，再增量鏡像到目標機）。
#
# Usage:
#   .\scripts\migrate_daily.ps1              # 真的增量鏡像 + 驗證（--apply --verify）
#   .\scripts\migrate_daily.ps1 -DryRun      # 只預覽（robocopy /L），不動目標
#
# 認證：走「執行本腳本的 Windows 帳號」現有工作階段。無人值守前，請先存憑證一次：
#   cmdkey /add:192.168.11.33 /user:DESKTOP-B2S6D9K\User /pass:<密碼>
#
# Exit codes: 0 ok；10 locked（另一個實例在跑）；20 找不到 bash；
#             其餘沿用 migrate_to_host.sh 的退出碼。

[CmdletBinding()]
param([switch]$DryRun)

Set-StrictMode -Off
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Repo

$LogDir = Join-Path $Repo 'meta\audit'
$Today  = Get-Date -Format 'yyyy-MM-dd'
$Log    = Join-Path $LogDir "migrate_$Today.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log([string]$lvl, [string]$msg) {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'o'), $lvl, $msg
    Add-Content -Path $Log -Value $line -Encoding utf8
    Write-Host $line
}

# ---- lock（stale-PID aware，與 daily_refresh 同風格；避免與遷移自身重疊）----
$LockFile = Join-Path $env:TEMP 'quantdata_migrate.lock'
if (Test-Path $LockFile) {
    $oldPid = Get-Content $LockFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Log WARN "another migrate is running (PID $oldPid) — exit"; exit 10
    }
}
$PID | Set-Content -Path $LockFile
try {
    Log INFO "==== migrate_daily start (repo=$Repo, dryrun=$DryRun) ===="

    if (-not (Test-Path (Join-Path $Repo 'scripts\migrate.conf'))) {
        Log ERROR "scripts\migrate.conf 不存在 — 先設好目標主機（見 migrate.conf.example）"; exit 1
    }

    # ---- 找 Git Bash（務必是 Git Bash，非 WSL / System32 的 bash.exe：----
    #      WSL bash 掛載在 /mnt/c 且無 cygpath/MSYS，Windows 分支會失敗）--------
    $bash = $null
    $cands = @(
        "$env:ProgramFiles\Git\bin\bash.exe",
        "$env:ProgramFiles\Git\usr\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
    )
    # 從 git.exe 位置推 Git 安裝根，補進候選（涵蓋非預設安裝路徑）
    $git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
    if ($git) {
        $gitRoot = Split-Path -Parent (Split-Path -Parent $git)   # ...\Git\cmd\git.exe -> ...\Git
        $cands += @("$gitRoot\bin\bash.exe", "$gitRoot\usr\bin\bash.exe")
    }
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { $bash = $c; break } }
    if (-not $bash) { Log ERROR "找不到 Git Bash（需安裝 Git for Windows；不可用 WSL bash）"; exit 20 }
    Log INFO "bash: $bash"

    # ---- 組 migrate_to_host.sh 參數 ---------------------------------------
    $flags = if ($DryRun) { '--os-type windows' } else { '--os-type windows --apply --verify' }
    # C:\QUANTDATA → /c/QUANTDATA（Git Bash 掛載點；drive letter 需小寫）
    $bashRepo = '/' + $Repo.Substring(0,1).ToLower() + ($Repo.Substring(2) -replace '\\','/')
    $cmd   = "cd '$bashRepo' && ./scripts/migrate_to_host.sh $flags"
    Log INFO "run: bash -lc `"$cmd`""

    & $bash -lc $cmd *>> $Log
    $rc = $LASTEXITCODE
    if ($rc -eq 0) { Log INFO "==== migrate_daily OK ====" }
    else           { Log ERROR "migrate_to_host.sh exit=$rc（見上方 log）" }
    exit $rc
}
finally {
    Remove-Item -Force $LockFile -ErrorAction SilentlyContinue
}
