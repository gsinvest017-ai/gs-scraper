# Public DuckDB UI tunnel via ngrok (Windows). Linux/WSL2: ngrok_tunnel.sh.
#
# One-time setup (USER MUST DO ONCE):
#   1. Sign up at https://dashboard.ngrok.com/signup (free tier OK)
#   2. ngrok config add-authtoken <YOUR_AUTHTOKEN>
#   3. (optional) reserve a static domain at https://dashboard.ngrok.com/domains
#
# Usage:
#   .\scripts\ngrok_tunnel.ps1 start [<port>]   # default 4213
#   .\scripts\ngrok_tunnel.ps1 stop
#   .\scripts\ngrok_tunnel.ps1 status
#   .\scripts\ngrok_tunnel.ps1 url
#
# Env vars: NGROK_DOMAIN, NGROK_BASIC_AUTH (user:pass), NGROK_CIDR_ALLOW.

[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Cmd = 'status',
    [Parameter(Position = 1)][int]$Port = 4213
)

$ErrorActionPreference = 'Stop'
$Root    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogFile = Join-Path $Root 'catalog\.ngrok.log'
$PidFile = Join-Path $Root 'catalog\.ngrok.pid'
$Policy  = Join-Path $Root 'catalog\.ngrok_policy.yml'

function Test-Running {
    if (-not (Test-Path $PidFile)) { return $false }
    $p = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue))
}

function Show-Url {
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 3
        $u = ($r.tunnels | Select-Object -First 1).public_url
        if ($u) { Write-Host "public URL: $u" }
        else    { Write-Host "(no tunnel URL yet — ngrok may still be starting; check $LogFile)" }
    } catch {
        Write-Host "(ngrok API not reachable yet; check $LogFile)"
    }
}

switch ($Cmd) {
    'start' {
        if (Test-Running) { Write-Host "ngrok already running (PID $(Get-Content $PidFile))"; Show-Url; break }
        if (-not (Get-Command ngrok.exe -ErrorAction SilentlyContinue)) {
            Write-Host 'ERROR: ngrok not in PATH; install first' -ForegroundColor Red; exit 1
        }
        & ngrok.exe config check *>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'ERROR: ngrok not configured. Run: ngrok config add-authtoken <YOUR_TOKEN>' -ForegroundColor Red
            exit 1
        }
        $ngArgs = @('http', "$Port", '--log=stdout')
        if ($env:NGROK_DOMAIN) { $ngArgs += "--url=$($env:NGROK_DOMAIN)" }
        if ($env:NGROK_BASIC_AUTH -or $env:NGROK_CIDR_ALLOW) {
            $lines = @('on_http_request:')
            if ($env:NGROK_CIDR_ALLOW) {
                $lines += '  - expressions:'
                $lines += "      - `"!(conn.client_ip.matches('$($env:NGROK_CIDR_ALLOW)'))`""
                $lines += '    actions:'
                $lines += '      - type: deny'
            }
            if ($env:NGROK_BASIC_AUTH) {
                $lines += '  - actions:'
                $lines += '      - type: basic-auth'
                $lines += '        config:'
                $lines += '          credentials:'
                $lines += "            - `"$($env:NGROK_BASIC_AUTH)`""
            }
            $lines | Set-Content -Path $Policy -Encoding utf8
            $ngArgs += "--traffic-policy-file=$Policy"
        }
        Write-Host "starting: ngrok $($ngArgs -join ' ') (log: $LogFile)"
        $proc = Start-Process -FilePath ngrok.exe -ArgumentList $ngArgs `
                  -RedirectStandardOutput $LogFile -RedirectStandardError "$LogFile.err" `
                  -WindowStyle Hidden -PassThru
        $proc.Id | Set-Content -Path $PidFile
        Start-Sleep -Seconds 3
        if (-not (Test-Running)) {
            Write-Host "ERROR: ngrok exited; check $LogFile" -ForegroundColor Red
            Remove-Item -Force $PidFile -ErrorAction SilentlyContinue; exit 1
        }
        Write-Host "ngrok PID $(Get-Content $PidFile)"; Show-Url
    }
    'stop' {
        if (-not (Test-Running)) { Write-Host 'ngrok not running'; Remove-Item -Force $PidFile -ErrorAction SilentlyContinue; break }
        Stop-Process -Id (Get-Content $PidFile) -Force -ErrorAction SilentlyContinue
        Remove-Item -Force $PidFile, $Policy -ErrorAction SilentlyContinue
        Write-Host 'stopped'
    }
    'status' {
        if (Test-Running) { Write-Host "ngrok running (PID $(Get-Content $PidFile))"; Show-Url }
        else              { Write-Host 'ngrok not running' }
    }
    'url' { Show-Url }
    default { Write-Host "Usage: .\scripts\ngrok_tunnel.ps1 {start|stop|status|url} [port]" -ForegroundColor Red; exit 2 }
}
