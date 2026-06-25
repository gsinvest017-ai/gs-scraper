# QUANTDATA Search UI launcher (Windows) — runs Flask on 0.0.0.0:5050.
# Linux / macOS / WSL2: use run_search_ui.sh.

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Repo   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Repo

$VenvPy = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
    Write-Host "[search-ui] error: $VenvPy not found — run '.\run.ps1 setup' first" -ForegroundColor Red
    exit 1
}

# Sanity check: catalog must exist
if (-not (Test-Path (Join-Path $Repo 'catalog\quant.duckdb'))) {
    Write-Host "[search-ui] error: catalog\quant.duckdb not found — run 'qd-ingest build-catalog' first" -ForegroundColor Red
    exit 1
}

# Flask installed?
& $VenvPy -c 'import flask' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[search-ui] installing flask..."
    & $VenvPy -m pip install flask
}

# Migration dashboard 的密碼認證在 Linux 走 sshpass；Windows 沒有 sshpass。
# 改用 OpenSSH (ssh.exe) 的 key 免密，或在 /migrate 頁面改走 key 認證。
if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    Write-Host "[search-ui] note: 未找到 ssh.exe — Migration 頁面遷移功能需要 OpenSSH。"
    Write-Host "[search-ui]       安裝：設定 > 應用程式 > 選用功能 > OpenSSH 用戶端，或用 ssh key 免密。"
}

Write-Host "[search-ui] starting at http://127.0.0.1:5050"
Write-Host "[search-ui]   . /         資料表清單"
Write-Host "[search-ui]   . /live     當日增量爬蟲即時監控（實盤監控模組）"
Write-Host "[search-ui]   . /migrate  Data migration dashboard"
& $VenvPy -m ui.search.app
