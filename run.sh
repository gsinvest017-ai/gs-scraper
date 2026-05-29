#!/usr/bin/env bash
# QUANTDATA one-button launcher (Linux / macOS / WSL2).
# Windows: use run.ps1.
#
# Usage:
#   ./run.sh             # show menu
#   ./run.sh setup       # create .venv + install pyproject (idempotent)
#   ./run.sh ui          # start Search UI (Flask, http://0.0.0.0:5050)
#   ./run.sh dashboard   # regen gap_dashboard.html
#   ./run.sh ingest      # run daily refresh (TEJ + macro + TAIFEX + derived)
#   ./run.sh test        # pytest -q
#   ./run.sh shell       # open DuckDB CLI on catalog
#   ./run.sh -h | --help # this message

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY_MIN="3.11"

log()  { printf "[run] %s\n" "$*"; }
fail() { printf "[run] ERROR: %s\n" "$*" >&2; exit 1; }

find_python() {
  # Prefer python3.12 / 3.11 explicit binaries, fall back to python3 / python.
  for cand in python3.12 python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      ver=$("$cand" -c 'import sys; print("{}.{}".format(*sys.version_info[:2]))' 2>/dev/null || echo "")
      if [ -n "$ver" ]; then
        # naive >= compare via sort -V
        if [ "$(printf '%s\n%s\n' "$PY_MIN" "$ver" | sort -V | head -1)" = "$PY_MIN" ]; then
          echo "$cand"; return 0
        fi
      fi
    fi
  done
  return 1
}

ensure_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    log "no .venv — bootstrapping"
    py=$(find_python) || fail "Python >= $PY_MIN not found. Install python3.11+ first."
    "$py" -m venv "$VENV"
  fi
  if ! "$VENV/bin/python" -c "import qd_ingest" 2>/dev/null; then
    log "installing project (editable + ingest extras)"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -e ".[ingest]" || \
      fail "pip install failed — run '$VENV/bin/pip install -e .[ingest]' manually to see error"
  fi
}

cmd_setup()     { ensure_venv; log "setup complete — venv at $VENV"; }
cmd_ui()        { ensure_venv; exec "$VENV/bin/python" -m ui.search.app; }
cmd_dashboard() { ensure_venv; exec "$VENV/bin/python" scripts/gap_report.py --format all; }
cmd_ingest()    { ensure_venv; exec bash scripts/daily_refresh.sh; }
cmd_test()      { ensure_venv; exec "$VENV/bin/python" -m pytest -q tests/; }
cmd_shell() {
  if ! command -v duckdb >/dev/null 2>&1; then
    fail "duckdb CLI not on PATH — see https://duckdb.org/docs/installation/"
  fi
  exec duckdb catalog/quant.duckdb
}

usage() {
  cat <<EOF
QUANTDATA launcher

Subcommands:
  setup       create .venv + install pyproject (idempotent)
  ui          start Search UI on http://0.0.0.0:5050
  dashboard   regen docs/gap_dashboard.html
  ingest      run daily TEJ + macro + TAIFEX + derived refresh
  test        pytest -q
  shell       open DuckDB CLI on catalog/quant.duckdb

Examples:
  ./run.sh                # show menu (interactive)
  ./run.sh ui             # 1-shot start UI
  ./run.sh setup          # just bootstrap venv
EOF
}

if [ $# -eq 0 ]; then
  usage
  printf "\nSelect [setup/ui/dashboard/ingest/test/shell/q]: "
  read -r choice
  [ -z "$choice" ] || [ "$choice" = "q" ] && exit 0
  set -- "$choice"
fi

case "${1:-}" in
  setup)      cmd_setup ;;
  ui)         cmd_ui ;;
  dashboard)  cmd_dashboard ;;
  ingest)     cmd_ingest ;;
  test)       cmd_test ;;
  shell)      cmd_shell ;;
  -h|--help|help) usage ;;
  *) fail "unknown command: $1 (run './run.sh --help')" ;;
esac
