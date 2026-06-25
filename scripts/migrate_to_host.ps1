# QUANTDATA 一鍵跨主機 migrate — Windows wrapper。
#
# 真正的搬運邏輯在 scripts/migrate_to_host.sh（rsync + ssh + duckdb，全是 Linux
# 工具，而 18G 資料湖也住在 WSL 的 ext4）。本 wrapper 只負責把參數轉進 WSL 執行。
#
# 前置：Windows 已安裝 WSL2 + 一個 Linux distro，且該 distro 內這個 repo 路徑可達
#       （原生在 WSL：例如 /home/<you>/gs-scraper/QUANTDATA）。
#
# Usage（參數與 .sh 完全相同，原樣轉發）：
#   .\scripts\migrate_to_host.ps1                       # DRY-RUN 預覽
#   .\scripts\migrate_to_host.ps1 --apply               # 真的傳輸
#   .\scripts\migrate_to_host.ps1 --apply --verify      # 傳輸後驗證
#   .\scripts\migrate_to_host.ps1 --verify-only
#   .\scripts\migrate_to_host.ps1 --host kevin@192.168.0.50 --apply
#
# 目標主機一樣可寫在 WSL 端的 scripts/migrate.conf。

[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)

$ErrorActionPreference = 'Stop'

function Fail ([string]$m) { Write-Host "[migrate.ps1] ERROR: $m" -ForegroundColor Red; exit 1 }

# 找 wsl.exe
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) { Fail "找不到 wsl.exe。請先安裝 WSL2（wsl --install），或直接在 WSL/Linux 用 scripts/migrate_to_host.sh。" }

# 本 .ps1 所在的 scripts/ 的上層 = repo root（Windows 視角）
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# 轉成 WSL 路徑（同時支援 /mnt/c 的 Windows 端 checkout 與 \\wsl$ 原生路徑）
$WslRoot = (& wsl.exe wslpath -a "$Root" 2>$null)
if ([string]::IsNullOrWhiteSpace($WslRoot)) {
  Fail "無法把 '$Root' 轉成 WSL 路徑。若 repo 原生在 WSL，請改在 WSL shell 內直接執行 scripts/migrate_to_host.sh。"
}
$WslRoot = $WslRoot.Trim()

# 組遠端指令：cd 進 repo，跑 bash 腳本，原樣帶入參數
$argStr = ''
if ($Args) { $argStr = ($Args -join ' ') }

Write-Host "[migrate.ps1] 透過 WSL 執行：$WslRoot/scripts/migrate_to_host.sh $argStr"
& wsl.exe bash -lc "cd '$WslRoot' && ./scripts/migrate_to_host.sh $argStr"
exit $LASTEXITCODE
