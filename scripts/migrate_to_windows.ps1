# migrate_to_windows.ps1 — 一鍵把 QUANTDATA 從 WSL2 遷移到 Windows native。
#
# 本腳本「在 Windows 端執行」，從 \\wsl$\<distro>\... 把整個 repo（程式碼 +
# .git + 資料湖 bronze/silver/gold + catalog）robocopy 過來，然後重建 venv、
# 重生 catalog、驗收。同機 WSL→Windows 不需要 SSH / rsync。
#
# 因為它本質上是 Windows 端的 bootstrap（反向從 WSL 拉），沒有對應 .sh；
# 跨主機 Linux→Linux 的鏡像請改用 scripts/migrate_to_host.sh。
#
# Usage:
#   .\scripts\migrate_to_windows.ps1                 # DRY-RUN 預覽（預設，不動目標）
#   .\scripts\migrate_to_windows.ps1 -Apply          # 真的執行
#   .\scripts\migrate_to_windows.ps1 -Apply -Distro Ubuntu -Target D:\QUANTDATA
#   .\scripts\migrate_to_windows.ps1 -Apply -SkipVenv -SkipCatalog   # 只搬檔
#   .\scripts\migrate_to_windows.ps1 -Install        # 裝 qd-migrate 捷徑(免 cd)
#
# 便利捷徑：跑一次 -Install 後，開新 PowerShell 視窗即可任何位置直接打：
#   qd-migrate            # dry-run
#   qd-migrate -Apply     # 真的遷移
#
# 安全網：預設 DRY-RUN；robocopy /MIR 會「精確鏡像」（刪除目標多出來的檔），
# 所以 -Apply 前務必確認 -Target 是對的空目錄或既有 QUANTDATA。

[CmdletBinding()]
param(
    [string]$Distro,                                   # WSL distro 名；留空自動偵測
    [string]$Source,                                   # 來源；留空由 distro 組出
    [string]$Target = 'C:\QUANTDATA',                  # 目標 repo 路徑
    [string]$WslUser = 'kevin',                        # WSL 端使用者（組來源路徑用）
    [switch]$Apply,                                    # 不加 = dry-run
    [switch]$SkipVenv,                                 # 跳過 venv 重建
    [switch]$SkipCatalog,                              # 跳過 catalog 重生
    [switch]$RunTests,                                 # 收尾跑 pytest
    [switch]$Install                                   # 裝 qd-migrate 捷徑到 $PROFILE 後退出
)

$ErrorActionPreference = 'Stop'
function Log  ([string]$m) { Write-Host "[migrate-win] $m" }
function Warn ([string]$m) { Write-Host "[migrate-win] WARN: $m" -ForegroundColor Yellow }
function Fail ([string]$m) { Write-Host "[migrate-win] ERROR: $m" -ForegroundColor Red; exit 1 }

# ---- -Install：把 qd-migrate function 寫進 $PROFILE，之後任何位置可直接呼叫 ----
if ($Install) {
    $self = $PSCommandPath
    if (-not $self) { Fail '無法取得腳本自身路徑（請用 -File 方式呼叫）' }
    $begin = '# >>> qd-migrate <<<'
    $end   = '# <<< qd-migrate >>>'
    $block = @"
$begin
function qd-migrate {
    Set-ExecutionPolicy -Scope Process Bypass -Force -ErrorAction SilentlyContinue
    & '$self' @args
}
$end
"@
    if (-not (Test-Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force | Out-Null }
    # 移除舊 block（idempotent），再附上新的
    $lines = Get-Content $PROFILE -ErrorAction SilentlyContinue
    $kept = @(); $skip = $false
    foreach ($ln in $lines) {
        if ($ln -eq $begin) { $skip = $true; continue }
        if ($ln -eq $end)   { $skip = $false; continue }
        if (-not $skip) { $kept += $ln }
    }
    Set-Content -Path $PROFILE -Value ($kept + ($block -split "`r?`n")) -Encoding utf8
    Log "已安裝 qd-migrate 到 $PROFILE"
    Log "  指向: $self"
    Log "開新的 PowerShell 視窗（或執行  . `$PROFILE  ）後即可任何位置使用:"
    Log "    qd-migrate            # dry-run 預覽"
    Log "    qd-migrate -Apply     # 真的遷移"
    exit 0
}

# ---- 0. 解析來源（\\wsl$\<distro>\home\<user>\gs-scraper\QUANTDATA）--------
if (-not $Source) {
    if (-not $Distro) {
        try {
            # wsl.exe -l -q 輸出是 UTF-16；轉碼後取第一個（預設）distro
            $prev = [Console]::OutputEncoding
            [Console]::OutputEncoding = [System.Text.Encoding]::Unicode
            $Distro = (& wsl.exe -l -q 2>$null | Where-Object { $_.Trim() } | Select-Object -First 1).Trim()
            [Console]::OutputEncoding = $prev
        } catch { }
        if (-not $Distro) { Fail "無法自動偵測 WSL distro，請用 -Distro <name>（wsl -l -q 可查）" }
        Log "自動偵測到 WSL distro: $Distro"
    }
    $Source = "\\wsl`$\$Distro\home\$WslUser\gs-scraper\QUANTDATA"
}

if (-not (Test-Path $Source)) { Fail "來源不存在: $Source（檢查 -Distro / -WslUser，或 WSL 是否在執行）" }
Log "來源: $Source"
Log "目標: $Target"
if ($Apply) { Log '模式: APPLY（真的執行）' } else { Log '模式: DRY-RUN（僅預覽，加 -Apply 才動作）' }

# ---- 1. robocopy 鏡像 -----------------------------------------------------
if (-not (Get-Command robocopy.exe -ErrorAction SilentlyContinue)) { Fail 'robocopy 不存在' }

# /MIR 精確鏡像；排除 venv / 暫存 / 編譯產物（.git 保留以帶歷史）
$rcArgs = @($Source, $Target, '/MIR',
            '/XD', '.venv', '__pycache__', 'tmp', '_staging',
            '/XF', '*.pyc',
            '/R:2', '/W:5', '/NP')
if (-not $Apply) { $rcArgs += '/L' }   # /L = list only（dry-run，不寫任何東西）

Log "robocopy $($rcArgs -join ' ')"
& robocopy.exe @rcArgs | Out-Host
# robocopy exit code 0-7 視為成功（8+ 才是錯誤）
$rc = $LASTEXITCODE
if ($rc -ge 8) { Fail "robocopy 失敗（exit=$rc，見上方輸出）" }
Log "robocopy 完成（exit=$rc）"

if (-not $Apply) {
    Log 'DRY-RUN 結束。確認上面的檔案清單無誤後，加 -Apply 真的執行。'
    exit 0
}

# ---- 2. 重建 venv ---------------------------------------------------------
$VenvPy = Join-Path $Target '.venv\Scripts\python.exe'
if ($SkipVenv) {
    Log 'SkipVenv：跳過 venv 重建'
} else {
    Log '重建 venv（py -3.12 -m venv .venv → pip install -e ".[ingest]")'
    Push-Location $Target
    try {
        $py = if (Get-Command py -ErrorAction SilentlyContinue) { 'py -3.12' } else { 'python' }
        Invoke-Expression "$py -m venv .venv"
        & $VenvPy -m pip install --quiet --upgrade pip
        & $VenvPy -m pip install --quiet -e ".[ingest]"
        if ($LASTEXITCODE -ne 0) { Fail "pip install 失敗 — 手動跑 '$VenvPy -m pip install -e .[ingest]' 看錯誤" }
        & $VenvPy -c 'import duckdb, pyarrow, pandas; print("deps OK")'
    } finally { Pop-Location }
}

# ---- 3. 重生 catalog ------------------------------------------------------
if ($SkipCatalog) {
    Log 'SkipCatalog：跳過 catalog 重生'
} elseif (Test-Path $VenvPy) {
    Log '重生 catalog（python -m qd_ingest.cli build-catalog）'
    Push-Location $Target
    try {
        & $VenvPy -m qd_ingest.cli build-catalog
        if ($LASTEXITCODE -ne 0) { Warn 'build-catalog 失敗 — 手動重跑以恢復 view' }
    } finally { Pop-Location }
} else {
    Warn "找不到 $VenvPy，跳過 catalog 重生（先 -SkipCatalog:`$false 並確認 venv）"
}

# ---- 4. 驗收 --------------------------------------------------------------
Log '驗收：'
$pq = (Get-ChildItem -Recurse (Join-Path $Target 'silver'),(Join-Path $Target 'gold'),(Join-Path $Target 'bronze') -Filter *.parquet -ErrorAction SilentlyContinue).Count
Log "  parquet 檔數: $pq"
if (Test-Path (Join-Path $Target '.git')) {
    Push-Location $Target
    try {
        Log "  git HEAD: $(& git rev-parse --short HEAD 2>$null)"
        Warn 'robocopy 帶來的 .git 在 Windows 上可能因 EOL 顯示變更；可跑 git status / git checkout -- . 校正'
    } finally { Pop-Location }
}
if ($RunTests -and (Test-Path $VenvPy)) {
    Log '  pytest:'
    Push-Location $Target
    try { & $VenvPy -m pytest -q tests\ } finally { Pop-Location }
}

Log "完成 ✅  接著可: cd $Target ; .\run.ps1 ui  → http://127.0.0.1:5050/"
Log '驗收通過前，WSL 端原始資料先別刪。'
