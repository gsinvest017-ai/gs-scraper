# QUANTDATA one-button launcher (Windows PowerShell 5.1+ / pwsh 7+).
# Linux / macOS / WSL2: use run.sh.
#
# Usage:
#   .\run.ps1                  # show menu (interactive)
#   .\run.ps1 setup            # create .venv + install pyproject
#   .\run.ps1 ui               # start Search UI on http://0.0.0.0:5050
#   .\run.ps1 dashboard        # regen gap_dashboard.html
#   .\run.ps1 ingest           # run daily refresh (scripts\daily_refresh.ps1)
#   .\run.ps1 test             # pytest -q
#   .\run.ps1 -h | -help

[CmdletBinding()]
param([Parameter(Position = 0)][string]$Cmd)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Venv     = Join-Path $Root '.venv'
$VenvPy   = Join-Path $Venv 'Scripts\python.exe'
$VenvPip  = Join-Path $Venv 'Scripts\pip.exe'
$PyMinMaj = 3
$PyMinMin = 11

function Log  ([string]$m) { Write-Host "[run] $m" }
function Fail ([string]$m) { Write-Host "[run] ERROR: $m" -ForegroundColor Red; exit 1 }

function Find-Python {
  foreach ($cand in 'py -3.12','py -3.11','py -3','python3','python') {
    $parts = $cand -split ' ',2
    $exe   = $parts[0]
    $args  = if ($parts.Count -gt 1) { $parts[1] } else { $null }
    try {
      $verCmd = if ($args) { "$exe $args -c `"import sys; print('{}.{}'.format(*sys.version_info[:2]))`"" }
                else      { "$exe -c `"import sys; print('{}.{}'.format(*sys.version_info[:2]))`"" }
      $ver = Invoke-Expression $verCmd 2>$null
      if ($ver -match '^(\d+)\.(\d+)$') {
        $maj = [int]$Matches[1]; $min = [int]$Matches[2]
        if ($maj -gt $PyMinMaj -or ($maj -eq $PyMinMaj -and $min -ge $PyMinMin)) {
          return $cand
        }
      }
    } catch {}
  }
  return $null
}

function Ensure-Venv {
  if (-not (Test-Path $VenvPy)) {
    Log 'no .venv — bootstrapping'
    $py = Find-Python
    if (-not $py) { Fail "Python >= $PyMinMaj.$PyMinMin not found. Install Python 3.11+ from python.org." }
    Invoke-Expression "$py -m venv `"$Venv`""
  }
  $hasPkg = & $VenvPy -c 'import qd_ingest' 2>$null; $rc = $LASTEXITCODE
  if ($rc -ne 0) {
    Log 'installing project (editable + ingest extras)'
    & $VenvPip install --quiet --upgrade pip
    & $VenvPip install --quiet -e ".[ingest]"
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed — run '$VenvPip install -e .[ingest]' manually" }
  }
}

function Cmd-Setup     { Ensure-Venv; Log "setup complete — venv at $Venv" }
function Cmd-UI        { Ensure-Venv; & $VenvPy -m ui.search.app }
function Cmd-Dashboard { Ensure-Venv; & $VenvPy scripts\gap_report.py --format all }
function Cmd-Ingest    { Ensure-Venv; & (Join-Path $Root 'scripts\daily_refresh.ps1') }
function Cmd-Test      { Ensure-Venv; & $VenvPy -m pytest -q tests\ }

function Show-Usage {
@"
QUANTDATA launcher (Windows)

Subcommands:
  setup       create .venv + install pyproject
  ui          start Search UI on http://0.0.0.0:5050
  dashboard   regen docs\gap_dashboard.html
  ingest      run daily refresh (TEJ + macro + ingest + catalog rebuild)
  test        pytest -q

Examples:
  .\run.ps1                # show menu (interactive)
  .\run.ps1 ui             # 1-shot start UI
  .\run.ps1 setup          # just bootstrap venv
"@
}

if (-not $Cmd) {
  Show-Usage
  $choice = Read-Host "`nSelect [setup/ui/dashboard/test/q]"
  if (-not $choice -or $choice -eq 'q') { exit 0 }
  $Cmd = $choice
}

switch ($Cmd) {
  'setup'     { Cmd-Setup }
  'ui'        { Cmd-UI }
  'dashboard' { Cmd-Dashboard }
  'ingest'    { Cmd-Ingest }
  'test'      { Cmd-Test }
  '-h'        { Show-Usage }
  '-help'     { Show-Usage }
  'help'      { Show-Usage }
  default     { Fail "unknown command: $Cmd (run '.\run.ps1 -h')" }
}
