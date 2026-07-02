# daily_refresh.ps1 — pull latest TEJ data + ingest to silver + rebuild catalog
# (Windows port of daily_refresh.sh). Designed for Windows Scheduled Task.
# Linux / macOS / WSL2: use daily_refresh.sh.
#
# Differences vs the bash version (platform-forced):
#   - flock        -> a lock file with a stale-PID check
#   - fish vars    -> TEJAPI_KEY must come from the environment (no fish on Windows)
#   - fuser        -> Get-NetTCPConnection probe is unreliable for file locks, so
#                     we always build catalog to staging then atomically swap
#   - mv           -> Move-Item
#
# Exit codes: 0 ok, 1 fetch failed, 2 ingest failed, 3 catalog failed,
#             10 locked (another instance running), 11 missing TEJAPI_KEY.

[CmdletBinding()]
param([switch]$DryRun)

Set-StrictMode -Off
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Repo

$LogDir = Join-Path $Repo 'meta\audit'
$Today  = Get-Date -Format 'yyyy-MM-dd'
$Log    = Join-Path $LogDir "daily_refresh_$Today.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$LockFile = Join-Path $env:TEMP 'quantdata_daily_refresh.lock'

function Log([string]$lvl, [string]$msg) {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'o'), $lvl, $msg
    Add-Content -Path $Log -Value $line -Encoding utf8
    Write-Host $line
}

# ---- 1. lock (stale-PID aware) -------------------------------------------
if (Test-Path $LockFile) {
    $oldPid = Get-Content $LockFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Host "another daily_refresh is running (PID $oldPid) — exit"
        exit 10
    }
}
$PID | Set-Content -Path $LockFile
try {
    Log INFO "==== daily_refresh start (repo=$Repo) ===="

    # ---- 2. Env -----------------------------------------------------------
    # per-machine 密鑰 fallback：目標機沒有 User env var，靠 scripts\secrets.local.ps1
    # （gitignored，隨 migrate 帶過去）。本機已設 User env var，此處不會覆蓋。
    $secrets = Join-Path $Repo 'scripts\secrets.local.ps1'
    if ((-not $env:TEJAPI_KEY) -and (Test-Path $secrets)) { . $secrets }
    if (-not $env:TEJAPI_KEY) {
        Log ERROR "TEJAPI_KEY not set in environment — abort (set it via [Environment]::SetEnvironmentVariable or scripts\secrets.local.ps1)"
        exit 11
    }
    if (-not $env:TEJAPI_BASE) { $env:TEJAPI_BASE = 'https://api.tej.com.tw' }

    $VenvPy = Join-Path $Repo '.venv\Scripts\python.exe'
    if (-not (Test-Path $VenvPy)) {
        Log ERROR ".venv\Scripts\python.exe not found at $VenvPy — abort"
        exit 11
    }

    # helper: run a python step, append stdout+stderr to $Log, return exit code
    function Invoke-Step([string[]]$ArgList) {
        & $VenvPy @ArgList *>> $Log
        return $LASTEXITCODE
    }

    $fetchExtra = @()
    if ($DryRun) { $fetchExtra = @('--dry-run'); Log INFO "DRY_RUN: fetch plan only; ingest+catalog skipped" }

    # ---- 3. Fetch ---------------------------------------------------------
    Log INFO "step 1/3: fetch_tej --table all --append-since-silver --mode merge"
    if ((Invoke-Step (@("$Repo\scripts\fetch_tej.py",'--table','all','--append-since-silver','--mode','merge') + $fetchExtra)) -ne 0) {
        Log ERROR "fetch_tej.py failed (see $Log)"; exit 1
    }

    Log INFO "step 1.5: fetch_macro.py (yfinance)"
    if ((Invoke-Step (@("$Repo\scripts\fetch_macro.py") + $fetchExtra)) -ne 0) {
        Log WARN "fetch_macro.py failed — non-fatal; macro_daily may lag"
    }

    # FinMind (optional). FinMind 是住在 WSL 的 live crawler（Linux venv + live
    # sqlite + 常駐 daemon），Windows 沒有 Windows venv。策略：
    #   1) 有 Windows venv → 直接用（少數已在 Windows 建 FinMind 環境的情況）
    #   2) 否則橋接到 WSL：用 WSL 的 FinMind venv 跑本機 /mnt/c 上的腳本，
    #      snapshot 直接寫進本機 Windows 的 bronze/finmind（重用 WSL 基礎設施）
    #   3) 兩者皆無 → skip（non-fatal）
    $finmindRepo = if ($env:FINMIND_REPO) { $env:FINMIND_REPO } else { Join-Path $Repo '..\FINMIND資料集' }
    $finmindPy   = Join-Path $finmindRepo '.venv\Scripts\python.exe'
    $wslFinmind  = if ($env:WSL_FINMIND_REPO) { $env:WSL_FINMIND_REPO } else { '/home/kevin/gs-scraper/FINMIND資料集' }
    if (Test-Path $finmindPy) {
        Log INFO "step 1.7: fetch_finmind.py (Windows venv, by-date incremental)"
        & $finmindPy "$Repo\scripts\fetch_finmind.py" @fetchExtra *>> $Log
        if ($LASTEXITCODE -ne 0) { Log WARN "fetch_finmind.py failed — non-fatal" }
    } elseif (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        # C:\QUANTDATA → /mnt/c/QUANTDATA（drive letter 小寫）
        $mnt     = '/mnt/' + $Repo.Substring(0,1).ToLower() + ($Repo.Substring(2) -replace '\\','/')
        $dryFlag = if ($DryRun) { '--dry-run' } else { '' }
        $wslPy   = "$wslFinmind/.venv/bin/python"
        $wslCmd  = "cd '$mnt' && [ -x '$wslPy' ] || exit 127; FINMIND_REPO='$wslFinmind' '$wslPy' scripts/fetch_finmind.py $dryFlag"
        Log INFO "step 1.7: fetch_finmind.py (WSL venv bridge → $wslFinmind)"
        & wsl.exe bash -lc $wslCmd *>> $Log
        $rc = $LASTEXITCODE
        if     ($rc -eq 127) { Log WARN "step 1.7 skipped: WSL FinMind venv 不存在 $wslPy（設 WSL_FINMIND_REPO / FINMIND_REPO）" }
        elseif ($rc -ne 0)   { Log WARN "fetch_finmind.py (WSL) failed rc=$rc — non-fatal" }
    } else {
        Log WARN "step 1.7 skipped: 無 Windows venv ($finmindPy) 也無 wsl.exe"
    }

    if ($DryRun) { Log INFO "==== daily_refresh DRY-RUN OK (skipped ingest + catalog) ===="; exit 0 }

    # ---- 4. Ingest CSV-backed tables -------------------------------------
    $rawBase = if ($env:QUANTDATA_RAW) { $env:QUANTDATA_RAW } else { Join-Path $Repo '..\RAW_SOURCES' }
    $rawDir  = Join-Path $rawBase 'TEJ資料'
    function Ingest-One([string]$cmd, [string]$csvName) {
        $csv = Join-Path $rawDir $csvName
        if (-not (Test-Path $csv)) { Log WARN "skip ${cmd}: $csv missing"; return 0 }
        Log INFO "ingest: qd-ingest $cmd --csv $csv"
        $rc = Invoke-Step @('-m','qd_ingest.cli',$cmd,'--csv',$csv)
        if ($rc -ne 0) { Log ERROR "qd-ingest $cmd failed" }
        return $rc
    }
    if ((Ingest-One 'tej-stock'      'TWN_EWPRCD_股價.csv')      -ne 0) { exit 2 }
    if ((Ingest-One 'tej-inst-stock' 'TWN_EWTINST1_三大法人.csv') -ne 0) { exit 2 }
    if ((Ingest-One 'tej-margin'     'TWN_EWGIN_融資融券.csv')    -ne 0) { exit 2 }

    Log INFO "step 2.5: ingest macro -> silver"
    if ((Invoke-Step @('-m','qd_ingest.sources.macro')) -ne 0) { Log WARN "macro ingest failed — non-fatal" }
    Log INFO "step 2.6: derive tw_inst_futures_daily"
    if ((Invoke-Step @('-m','qd_ingest.sources.taifex')) -ne 0) { Log WARN "taifex derive failed — non-fatal" }

    # ---- 5. Catalog rebuild (always build to staging then swap) ----------
    $catalog = Join-Path $Repo 'catalog\quant.duckdb'
    $staging = Join-Path $Repo 'catalog\quant_refresh.duckdb'
    Log INFO "step 3/3: build-catalog (staging -> swap)"
    if ((Invoke-Step @('-m','qd_ingest.cli','build-catalog','--db-path',$staging)) -ne 0) {
        Log ERROR "build-catalog failed"; exit 3
    }
    if (Test-Path $catalog) { Move-Item -Force $catalog "$catalog.prev" }
    Move-Item -Force $staging $catalog
    Remove-Item -Force "$catalog.prev" -ErrorAction SilentlyContinue
    Log INFO "catalog swapped: $catalog"

    # ---- 5.x restore views + RAW-backed ingests + derived gold ----------
    $extra = @(
        @('restore views',        "$Repo\scripts\restore_finmind_views.py"),
        @('refresh continuous',   "$Repo\scripts\refresh_continuous_from_raw.py"),
        @('ingest bars_1m',       "$Repo\scripts\ingest_bars_1m.py"),
        @('ingest rf_daily',      "$Repo\scripts\ingest_rf_daily.py"),
        @('ingest txo_1min',      "$Repo\scripts\ingest_txo_1min.py"),
        @('ingest inst_market',   "$Repo\scripts\ingest_inst_market_daily.py")
    )
    foreach ($e in $extra) {
        Log INFO ("step 3.x: {0}" -f $e[0])
        if ((Invoke-Step @($e[1])) -ne 0) { Log WARN ("{0} failed — non-fatal" -f $e[0]) }
    }
    Log INFO "step 3.7: rebuild derived gold"
    if ((Invoke-Step @('-m','qd_ingest.sources.derived')) -ne 0) { Log WARN "derived gold rebuild failed — non-fatal" }

    # ---- 6. Regenerate gap dashboard ------------------------------------
    Log INFO "step 4/4: regenerate gap dashboard"
    Invoke-Step @("$Repo\scripts\gap_report.py",'--format','all','--no-color') | Out-Null

    Log INFO "==== daily_refresh OK ===="
    exit 0
}
finally {
    Remove-Item -Force $LockFile -ErrorAction SilentlyContinue
}
