# Public DuckDB UI tunnel via Tailscale Funnel (Windows). Linux/WSL2: tailscale_funnel.sh.
#
# Prerequisites (one-time, done by USER on the tailnet admin + this node):
#   1. Tailnet admin: enable Funnel  https://login.tailscale.com/admin/acls
#   2. Tailnet admin: enable HTTPS certificates  https://login.tailscale.com/admin/dns
#   (Windows runs tailscale as a service, so no `--operator` step is needed.)
#
# Usage:
#   .\scripts\tailscale_funnel.ps1 check
#   .\scripts\tailscale_funnel.ps1 start [<port>]   # default 4213
#   .\scripts\tailscale_funnel.ps1 stop
#   .\scripts\tailscale_funnel.ps1 status
#   .\scripts\tailscale_funnel.ps1 url

[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Cmd = 'status',
    [Parameter(Position = 1)][int]$Port = 4213
)

$ErrorActionPreference = 'Stop'

# Locate tailscale.exe (PATH, or the default install dir).
$ts = (Get-Command tailscale.exe -ErrorAction SilentlyContinue).Source
if (-not $ts) {
    $default = 'C:\Program Files\Tailscale\tailscale.exe'
    if (Test-Path $default) { $ts = $default }
}
if (-not $ts) { Write-Host 'ERROR: tailscale.exe not found (install Tailscale for Windows)' -ForegroundColor Red; exit 1 }

function Get-DnsName {
    try {
        $j = & $ts status --self --json 2>$null | ConvertFrom-Json
        return ($j.Self.DNSName -replace '\.$','')
    } catch { return $null }
}

switch ($Cmd) {
    'check' {
        Write-Host '1. tailscale daemon up + logged in'
        & $ts status *>$null
        if ($LASTEXITCODE -eq 0) { Write-Host "     OK — node DNS: $(Get-DnsName)" }
        else { Write-Host '     FAIL — start Tailscale / log in' }
        Write-Host '2. tailnet Funnel attribute + HTTPS cert: check tailnet admin console'
        Write-Host '   https://login.tailscale.com/admin/acls  and  /admin/dns'
    }
    'start' {
        & $ts status *>$null
        if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: tailscale daemon not running' -ForegroundColor Red; exit 1 }
        $listening = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $listening) {
            Write-Host "ERROR: nothing listening on 127.0.0.1:$Port" -ForegroundColor Red
            Write-Host '  Run .\scripts\duckdb_public_ui.ps1 start  first.'
            exit 1
        }
        Write-Host "starting tailscale funnel for port $Port (background)..."
        $out = & $ts funnel --bg "$Port" 2>&1
        $rc  = $LASTEXITCODE
        Write-Host $out
        if ($rc -ne 0) {
            if ($out -match 'funnel is not enabled') {
                Write-Host 'DIAGNOSIS: Funnel not enabled on this node — enable in tailnet ACLs.' -ForegroundColor Yellow
            } elseif ($out -match 'cert') {
                Write-Host 'DIAGNOSIS: HTTPS certificates not enabled on the tailnet — enable in /admin/dns.' -ForegroundColor Yellow
            } else {
                Write-Host 'Funnel start failed; see above.' -ForegroundColor Yellow
            }
            exit 1
        }
        Start-Sleep -Seconds 1
        $h = Get-DnsName; if ($h) { Write-Host "https://$h" }
    }
    'stop'   { & $ts funnel reset 2>&1 | Out-Host }
    'status' { & $ts funnel status 2>&1 | Out-Host }
    'url'    {
        $h = Get-DnsName
        if (-not $h) { Write-Host '(no tailscale DNS name)' -ForegroundColor Red; exit 1 }
        Write-Host "https://$h"
    }
    default { Write-Host "Usage: .\scripts\tailscale_funnel.ps1 {check|start|stop|status|url} [port]" -ForegroundColor Red; exit 2 }
}
