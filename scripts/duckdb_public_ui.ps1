# Manage a read-only DuckDB UI session against a snapshot of the catalog,
# for exposure through ngrok / Tailscale Funnel (Windows).
# Linux / macOS / WSL2: use duckdb_public_ui.sh.
#
#   start    — refuses if another duckdb -ui is already running; else snapshot + launch
#   replace  — stop existing duckdb -ui (if any) and take over
#   stop     — shut our UI down
#   status   — what's running, on what port
#   refresh  — re-snapshot from live catalog (needs stop+start to pick up)
#
# Why a snapshot: anyone who reaches the URL has full SQL access. The snapshot
# keeps the blast radius local and lets the tunnel keep serving while you
# re-ingest into the live catalog.
#
# Env: DUCKDB_PUBLIC_PORT (default 4214), DUCKDB_PUBLIC_READONLY=1 to force -readonly.

[CmdletBinding()]
param([Parameter(Position = 0)][string]$Cmd = 'status')

$ErrorActionPreference = 'Stop'
$Root     = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LiveDb   = Join-Path $Root 'catalog\quant.duckdb'
$PublicDb = Join-Path $Root 'catalog\quant_public.duckdb'
$PidFile  = Join-Path $Root 'catalog\.duckdb_public_ui.pid'
$LogFile  = Join-Path $Root 'catalog\.duckdb_public_ui.log'

if (-not (Get-Command duckdb.exe -ErrorAction SilentlyContinue)) {
    Write-Host 'ERROR: duckdb.exe not found in PATH (install the DuckDB CLI)' -ForegroundColor Red; exit 1
}

function Snapshot {
    if (-not (Test-Path $LiveDb)) { Write-Host "ERROR: live catalog not found at $LiveDb" -ForegroundColor Red; exit 1 }
    Copy-Item -Force $LiveDb "$PublicDb.tmp"
    Move-Item -Force "$PublicDb.tmp" $PublicDb
    Write-Host "snapshot refreshed: $PublicDb ($((Get-Item $PublicDb).Length) bytes)"
}

function Test-Running {
    if (-not (Test-Path $PidFile)) { return $false }
    $p = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue))
}

function Get-OtherUiPid {
    $ours = if (Test-Path $PidFile) { Get-Content $PidFile | Select-Object -First 1 } else { $null }
    Get-CimInstance Win32_Process -Filter "Name='duckdb.exe'" |
        Where-Object { $_.CommandLine -match '-ui' -and "$($_.ProcessId)" -ne "$ours" } |
        Select-Object -First 1 -ExpandProperty ProcessId
}

function Launch {
    Snapshot
    $duckArgs = @()
    if ($env:DUCKDB_PUBLIC_READONLY -eq '1') { $duckArgs += '-readonly' }
    $duckArgs += @('-ui', $PublicDb)
    Write-Host "launching duckdb $($duckArgs -join ' ') against snapshot (log: $LogFile)..."
    # Start-Process keeps it alive detached (no need for the bash stdin-keepalive trick).
    $proc = Start-Process -FilePath duckdb.exe -ArgumentList $duckArgs `
              -RedirectStandardOutput $LogFile -RedirectStandardError "$LogFile.err" `
              -WindowStyle Hidden -PassThru
    $proc.Id | Set-Content -Path $PidFile
    Start-Sleep -Seconds 2
    if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: duckdb -ui did not start; check $LogFile" -ForegroundColor Red
        Remove-Item -Force $PidFile -ErrorAction SilentlyContinue; return
    }
    $conn = Get-NetTCPConnection -State Listen -OwningProcess $proc.Id -ErrorAction SilentlyContinue | Select-Object -First 1
    $port = if ($conn) { $conn.LocalPort } else { '?' }
    Write-Host "public UI PID $($proc.Id), listening on 127.0.0.1:$port"
    Write-Host ''
    Write-Host 'Tunnel it with one of:'
    Write-Host "  .\scripts\tailscale_funnel.ps1 start $port"
    Write-Host "  .\scripts\ngrok_tunnel.ps1 start $port"
}

switch ($Cmd) {
    'start' {
        if (Test-Running) { Write-Host "our public UI already running (PID $(Get-Content $PidFile))"; break }
        $other = Get-OtherUiPid
        if ($other) {
            Write-Host "ERROR: another duckdb -ui is running (PID $other)." -ForegroundColor Red
            Write-Host "  Stop it first or use '.\scripts\duckdb_public_ui.ps1 replace' to take over."
            exit 1
        }
        Launch
    }
    'replace' {
        if (Test-Running) { Write-Host "our public UI already running (PID $(Get-Content $PidFile))"; break }
        $other = Get-OtherUiPid
        if ($other) {
            Write-Host "stopping existing duckdb -ui PID $other..."
            Stop-Process -Id $other -Force -ErrorAction SilentlyContinue
        }
        Launch
    }
    'stop' {
        if (-not (Test-Running)) { Write-Host 'not running'; Remove-Item -Force $PidFile -ErrorAction SilentlyContinue; break }
        $p = Get-Content $PidFile
        Write-Host "stopping public UI PID $p"
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
    }
    'status' {
        if (Test-Running) {
            Write-Host "public UI running (PID $(Get-Content $PidFile))"
            if (Test-Path $PublicDb) { Write-Host "snapshot: $PublicDb ($((Get-Item $PublicDb).Length) bytes)" }
        } else {
            Write-Host 'public UI not running'
        }
    }
    'refresh' {
        Snapshot
        Write-Host 'NOTE: open clients must reconnect / refresh tab to see updated data'
    }
    default { Write-Host "Usage: .\scripts\duckdb_public_ui.ps1 {start|replace|stop|status|refresh}" -ForegroundColor Red; exit 2 }
}
