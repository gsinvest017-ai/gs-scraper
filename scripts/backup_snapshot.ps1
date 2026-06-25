# Backup the QUANTDATA lakehouse to a snapshot directory (Windows).
# Linux / macOS / WSL2: use backup_snapshot.sh (rsync).
#
# Usage:
#   .\scripts\backup_snapshot.ps1 [-TargetBase <dir>]
#
# Default target: D:\QUANTDATA-snapshots  (override with -TargetBase)
#
# Strategy:
#   - bronze/ silver/ gold/ reference/ catalog/ meta/ docs/ scripts/ tests/
#     + top-level *.md / *.toml / *.yaml are robocopy'd into <target>\<YYYY-MM-DD>\.
#   - robocopy /MIR mirrors incrementally (like rsync --delete).
#   - Junction <target>\latest -> <YYYY-MM-DD> refreshed.
#   - .git\ .venv\ __pycache__\ _staging\ are excluded.

[CmdletBinding()]
param([string]$TargetBase = 'D:\QUANTDATA-snapshots')

$ErrorActionPreference = 'Stop'
$Root  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Today = Get-Date -Format 'yyyy-MM-dd'
$Dest  = Join-Path $TargetBase $Today

if (-not (Get-Command robocopy.exe -ErrorAction SilentlyContinue)) {
    Write-Host 'robocopy not found — aborting.' -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Write-Host "[$(Get-Date -Format o)] backing up QUANTDATA -> $Dest"

# Mirror whole-directory data dirs (robocopy /MIR == rsync -a --delete per dir).
$dirs = 'bronze','silver','gold','reference','catalog','meta','docs','scripts','tests'
foreach ($d in $dirs) {
    $srcDir = Join-Path $Root $d
    if (Test-Path $srcDir) {
        robocopy $srcDir (Join-Path $Dest $d) /MIR /XD .venv .git __pycache__ _staging /XF *.pyc /NFL /NDL /NP | Out-Null
    }
}

# Top-level loose files (*.md / *.toml / *.yaml).
robocopy $Root $Dest *.md *.toml *.yaml /NFL /NDL /NP | Out-Null

# Refresh 'latest' junction (delete + recreate; junctions need no admin).
$latest = Join-Path $TargetBase 'latest'
if (Test-Path $latest) { (Get-Item $latest).Delete() }
New-Item -ItemType Junction -Path $latest -Target $Dest | Out-Null

Write-Host "[$(Get-Date -Format o)] done. Latest -> $latest"
$size = (Get-ChildItem -Recurse $Dest | Measure-Object -Property Length -Sum).Sum
Write-Host ("snapshot size: {0:N1} GB" -f ($size / 1GB))
