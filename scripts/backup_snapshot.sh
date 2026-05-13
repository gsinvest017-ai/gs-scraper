#!/usr/bin/env bash
# Backup the QUANTDATA lakehouse to a snapshot directory.
#
# Usage:
#   scripts/backup_snapshot.sh [<target-base-dir>]
#
# Default target: /mnt/d/QUANTDATA-snapshots  (WSL2 mount; override on macOS/Linux)
#
# Strategy:
#   - bronze/, silver/, gold/, reference/, catalog/, meta/, _quarantine/manifest_*.jsonl
#     are rsync'd into  <target>/<YYYY-MM-DD>/.
#   - Existing snapshot for today is updated incrementally (rsync --delete).
#   - Symlink  <target>/latest -> <YYYY-MM-DD>  refreshed.
#   - .git/, .venv/, __pycache__/, _staging/ are excluded.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_BASE="${1:-/mnt/d/QUANTDATA-snapshots}"
TODAY="$(date +%F)"
DEST="${TARGET_BASE}/${TODAY}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync not installed — aborting." >&2
  exit 1
fi

mkdir -p "${DEST}"
echo "[$(date -Is)] backing up QUANTDATA -> ${DEST}"

rsync -a --delete \
  --exclude=".git/" \
  --exclude=".venv/" \
  --exclude="__pycache__/" \
  --exclude="*.pyc" \
  --exclude="_staging/" \
  --include="bronze/***" \
  --include="silver/***" \
  --include="gold/***" \
  --include="reference/***" \
  --include="catalog/***" \
  --include="meta/***" \
  --include="_quarantine/manifest_*.jsonl" \
  --include="*.md" \
  --include="*.toml" \
  --include="*.yaml" \
  --include="src/***" \
  --include="scripts/***" \
  --include="tests/***" \
  --include="docs/***" \
  --exclude="*" \
  "${ROOT}/" "${DEST}/"

ln -sfn "${DEST}" "${TARGET_BASE}/latest"

echo "[$(date -Is)] done. Latest -> ${TARGET_BASE}/latest"
du -sh "${DEST}" 2>/dev/null || true
