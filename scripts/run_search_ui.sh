#!/usr/bin/env bash
# QUANTDATA Search UI launcher — runs Flask on 127.0.0.1:5050
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

VENV="$REPO/.venv/bin/python"
if [ ! -x "$VENV" ]; then
  echo "[search-ui] error: $VENV not found"; exit 1
fi

# Sanity check: catalog must exist
if [ ! -f "$REPO/catalog/quant.duckdb" ]; then
  echo "[search-ui] error: catalog/quant.duckdb not found — run 'qd-ingest build-catalog' first"; exit 1
fi

# Flask installed?
if ! "$VENV" -c 'import flask' 2>/dev/null; then
  echo "[search-ui] installing flask..."
  "$VENV" -m pip install flask
fi

echo "[search-ui] starting at http://127.0.0.1:5050"
exec "$VENV" -m ui.search.app
