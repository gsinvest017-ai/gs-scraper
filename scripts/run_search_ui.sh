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

# Migration dashboard 的 password 認證需要 sshpass（用 ssh key 免密則不需要）
if ! command -v sshpass >/dev/null 2>&1; then
  echo "[search-ui] note: 未裝 sshpass — Migration 頁面的『密碼登入』會無法使用。"
  echo "[search-ui]       要用密碼遷移請先：sudo apt install sshpass（或改用 ssh key 免密）。"
fi

echo "[search-ui] starting at http://127.0.0.1:5050"
echo "[search-ui]   · /         資料表清單"
echo "[search-ui]   · /migrate  Data migration dashboard"
exec "$VENV" -m ui.search.app
