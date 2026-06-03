#!/usr/bin/env bash
# QUANTDATA 一鍵跨主機 migrate — Approach A：單一 rsync-over-SSH 鏡像。
#
# 把整個 repo（程式碼 + .git + 18G 資料湖 bronze/silver/gold + DuckDB catalog）
# idempotent 鏡像到一台 SSH 可達的目標主機。DuckDB catalog 的 view 全用相對路徑
# (read_parquet('silver/...'))，所以目標端不需改任何 SQL，repo 樹一致即可開。
#
# Usage:
#   scripts/migrate_to_host.sh [options]            # DRY-RUN 預覽（預設，不動目標）
#   scripts/migrate_to_host.sh --apply              # 真的傳輸
#   scripts/migrate_to_host.sh --apply --verify     # 傳輸後做來源 vs 目標驗證
#   scripts/migrate_to_host.sh --verify-only        # 只比對，不傳輸
#
# 目標主機設定（優先序）：
#   1. CLI flag：   --host user@host   --path /remote/path   --port 22
#   2. config 檔：  scripts/migrate.conf （gitignored；見 migrate.conf.example）
#   3. 環境變數：   MIGRATE_HOST / MIGRATE_PATH / MIGRATE_SSH_PORT
#
# 選項：
#   --host U@H        目標 ssh 目的地（user@host）
#   --path P          目標端 repo 絕對路徑（預設沿用來源端絕對路徑）
#   --port N          ssh port（預設 22）
#   --apply           真的執行（不加則 dry-run）
#   --no-delete       不做 --delete（保留目標端多出來的檔；預設是精確鏡像）
#   --bwlimit N       rsync 頻寬上限（KB/s），跨 WAN 時用
#   --verify          傳輸後執行驗證
#   --verify-only     只跑驗證（來源 vs 目標），不傳輸
#   -h | --help       顯示說明
#
# 安全網：
#   - 預設 DRY-RUN，需 --apply 才寫任何東西到目標。
#   - catalog/quant.duckdb 被鎖（writer / `duckdb -ui`）時拒絕 --apply。
#   - --apply 前先 CHECKPOINT 把 WAL 落盤（WAL 本身 exclude 不傳）。
#   - 永不 git push、永不動本機資料湖；本腳本只「讀來源、寫遠端」。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log()  { printf "[migrate] %s\n" "$*"; }
warn() { printf "[migrate] WARN: %s\n" "$*" >&2; }
fail() { printf "[migrate] ERROR: %s\n" "$*" >&2; exit 1; }

# ---- 預設值 -------------------------------------------------------------
HOST=""
TARGET_PATH=""
SSH_PORT="22"
APPLY=0
DO_DELETE=1
BWLIMIT=""
DO_VERIFY=0
VERIFY_ONLY=0

CATALOG="catalog/quant.duckdb"

# ---- config 檔（可被 CLI / env 覆寫）-------------------------------------
CONF="$ROOT/scripts/migrate.conf"
if [[ -f "$CONF" ]]; then
  # shellcheck disable=SC1090
  source "$CONF"
  HOST="${MIGRATE_HOST:-$HOST}"
  TARGET_PATH="${MIGRATE_PATH:-$TARGET_PATH}"
  SSH_PORT="${MIGRATE_SSH_PORT:-$SSH_PORT}"
  BWLIMIT="${MIGRATE_BWLIMIT:-$BWLIMIT}"
fi
# env vars（優先於 config 檔）
HOST="${MIGRATE_HOST:-$HOST}"
TARGET_PATH="${MIGRATE_PATH:-$TARGET_PATH}"
SSH_PORT="${MIGRATE_SSH_PORT:-$SSH_PORT}"
BWLIMIT="${MIGRATE_BWLIMIT:-$BWLIMIT}"

# ---- 解析 CLI（最高優先）------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)        HOST="$2"; shift 2 ;;
    --path)        TARGET_PATH="$2"; shift 2 ;;
    --port)        SSH_PORT="$2"; shift 2 ;;
    --bwlimit)     BWLIMIT="$2"; shift 2 ;;
    --apply)       APPLY=1; shift ;;
    --no-delete)   DO_DELETE=0; shift ;;
    --verify)      DO_VERIFY=1; shift ;;
    --verify-only) VERIFY_ONLY=1; DO_VERIFY=1; shift ;;
    -h|--help)     awk 'NR>1 && /^#/{sub(/^# ?/,"");print;next} NR>1{exit}' "$0"; exit 0 ;;
    *)             fail "未知選項：$1（用 -h 看說明）" ;;
  esac
done

# 目標端路徑預設沿用來源端絕對路徑（同 layout，relative-path view 直接可用）
[[ -z "$TARGET_PATH" ]] && TARGET_PATH="$ROOT"

# ---- 共用：ssh / 目的地字串 ---------------------------------------------
SSH_CMD=(ssh -p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=10)
DEST="${HOST}:${TARGET_PATH%/}/"

# ===========================================================================
#  pre-flight
# ===========================================================================
preflight() {
  command -v rsync >/dev/null 2>&1 || fail "本機沒裝 rsync"
  command -v ssh   >/dev/null 2>&1 || fail "本機沒裝 ssh"
  [[ -n "$HOST" ]] || fail "未指定目標主機。用 --host user@host，或建 scripts/migrate.conf（見 migrate.conf.example）"
  [[ -f "$CATALOG" ]] || warn "找不到 $CATALOG（catalog 可能還沒建）"

  log "目標：$DEST  (ssh port $SSH_PORT)"
  log "測試 SSH 連線…"
  "${SSH_CMD[@]}" "$HOST" true 2>/dev/null \
    || fail "SSH 連不上 $HOST。先確認 ssh key 已設定（ssh -p $SSH_PORT $HOST true）"
  log "SSH OK"
}

# DuckDB 鎖檢查（CLAUDE.md：duckdb -ui 會鎖整個 catalog）
check_duckdb_lock() {
  [[ -f "$CATALOG" ]] || return 0
  if command -v fuser >/dev/null 2>&1; then
    local pids
    pids="$(fuser "$CATALOG" 2>/dev/null | tr -s ' ' || true)"
    if [[ -n "${pids// /}" ]]; then
      warn "catalog 被以下 PID 持有：$pids"
      if [[ "$APPLY" -eq 1 ]]; then
        fail "catalog 正被佔用（可能是 duckdb -ui / 寫入中）。先關掉再 --apply。"
      fi
    fi
  fi
}

# CHECKPOINT：把 WAL 落盤，這樣排除 *.duckdb.wal 也不會漏資料
checkpoint_duckdb() {
  [[ -f "$CATALOG" ]] || return 0
  command -v duckdb >/dev/null 2>&1 || { warn "找不到 duckdb CLI，跳過 CHECKPOINT"; return 0; }
  log "CHECKPOINT $CATALOG（落盤 WAL）…"
  if ! timeout 60 duckdb "$CATALOG" "CHECKPOINT;" >/dev/null 2>&1; then
    fail "CHECKPOINT 失敗（catalog 可能被鎖）。先關掉佔用 process 再試。"
  fi
}

# ===========================================================================
#  rsync
# ===========================================================================
EXCLUDES=(
  --exclude ".venv/"
  --exclude "venv/"
  --exclude "site/"
  --exclude "__pycache__/"
  --exclude "*.pyc"
  --exclude ".pytest_cache/"
  --exclude ".mypy_cache/"
  --exclude ".ruff_cache/"
  --exclude "*.duckdb.wal"
  --exclude "*.duckdb.tmp"
  --exclude "tmp/"
  --exclude "_staging/"
  --exclude ".claude/local/"
  --exclude "catalog/*.bak*"
  --exclude "catalog/*.log"
  --exclude "catalog/.ngrok.log"
)

run_rsync() {
  local opts=(-aH --human-readable --partial --info=progress2 --stats)
  [[ -n "$BWLIMIT" ]] && opts+=(--bwlimit "$BWLIMIT")
  [[ "$DO_DELETE" -eq 1 ]] && opts+=(--delete)
  if [[ "$APPLY" -eq 0 ]]; then
    opts+=(--dry-run)
    log "=== DRY-RUN（不會改目標；加 --apply 才真的傳）==="
  else
    log "=== APPLY：開始傳輸到 $DEST ==="
  fi

  "${SSH_CMD[@]}" "$HOST" "mkdir -p '${TARGET_PATH%/}'" \
    || fail "無法在目標端建立 $TARGET_PATH"

  rsync "${opts[@]}" "${EXCLUDES[@]}" \
    -e "ssh -p $SSH_PORT" \
    "$ROOT/" "$DEST"

  if [[ "$APPLY" -eq 0 ]]; then
    log "DRY-RUN 完成。確認上面的清單後，加 --apply 真的執行。"
  else
    log "傳輸完成。"
  fi
}

# ===========================================================================
#  verify（來源 vs 目標）
# ===========================================================================
LAYERS=(bronze silver gold reference)

# 核心 view row-count smoke：證明目標端能「透過 catalog 相對路徑讀到 parquet」，
# 不只是檔案有複製過去。挑 smoke_query.py 也用的穩定 view。
SMOKE_SQL="SELECT 'bars_1d' v, count(*) c FROM bars_1d \
UNION ALL SELECT 'symbol_map', count(*) FROM symbol_map \
UNION ALL SELECT 'macro_daily', count(*) FROM macro_daily \
UNION ALL SELECT 'stock_factor_daily', count(*) FROM stock_factor_daily"

layer_stat_local() {  # $1=layer -> "<files> <bytes>"
  local d="$1"
  if [[ -d "$ROOT/$d" ]]; then
    local n b
    n="$(find "$ROOT/$d" -type f | wc -l | tr -d ' ')"
    b="$(du -sb "$ROOT/$d" 2>/dev/null | cut -f1)"
    echo "$n $b"
  else
    echo "0 0"
  fi
}

do_verify() {
  log "=== 驗證：來源 vs 目標 per-layer 檔數 / 位元組 ==="

  # 目標端一次抓齊（find + du），用 heredoc 避免來回多次 ssh
  local remote_out
  remote_out="$("${SSH_CMD[@]}" "$HOST" "bash -s" <<REMOTE
set -e
cd '${TARGET_PATH%/}' 2>/dev/null || { echo "__NO_TARGET__"; exit 0; }
for d in ${LAYERS[*]}; do
  if [ -d "\$d" ]; then
    n=\$(find "\$d" -type f | wc -l | tr -d ' ')
    b=\$(du -sb "\$d" 2>/dev/null | cut -f1)
    echo "\$d \$n \$b"
  else
    echo "\$d 0 0"
  fi
done
if command -v duckdb >/dev/null 2>&1 && [ -f '${CATALOG}' ]; then
  v=\$(duckdb -readonly '${CATALOG}' "SELECT count(*) FROM duckdb_views() WHERE NOT internal;" -noheader -list 2>/dev/null | tr -d ' ')
  echo "__VIEWS__ \$v"
  duckdb -readonly '${CATALOG}' "${SMOKE_SQL}" -noheader -list 2>/dev/null \
    | while IFS='|' read -r vv cc; do echo "__ROW__ \$vv \$cc"; done || true
else
  echo "__VIEWS__ NA"
fi
REMOTE
)"

  if grep -q "__NO_TARGET__" <<<"$remote_out"; then
    fail "目標端找不到 $TARGET_PATH（先跑 --apply）"
  fi

  local ok=1
  printf "%-12s | %12s %14s | %12s %14s | %s\n" "layer" "src_files" "src_bytes" "dst_files" "dst_bytes" "match"
  printf -- "-------------+--------------------------------+--------------------------------+------\n"
  local d
  for d in "${LAYERS[@]}"; do
    read -r sn sb < <(layer_stat_local "$d")
    local line dn db
    line="$(grep -E "^$d " <<<"$remote_out" || echo "$d 0 0")"
    dn="$(awk '{print $2}' <<<"$line")"
    db="$(awk '{print $3}' <<<"$line")"
    local m="OK"
    if [[ "$sn" != "$dn" || "$sb" != "$db" ]]; then m="DIFF"; ok=0; fi
    printf "%-12s | %12s %14s | %12s %14s | %s\n" "$d" "$sn" "$sb" "$dn" "$db" "$m"
  done

  # catalog smoke：本機 view 數 vs 目標 view 數
  local src_views dst_views
  if command -v duckdb >/dev/null 2>&1 && [[ -f "$CATALOG" ]]; then
    src_views="$(duckdb -readonly "$CATALOG" "SELECT count(*) FROM duckdb_views() WHERE NOT internal;" -noheader -list 2>/dev/null | tr -d ' ')"
  else
    src_views="NA"
  fi
  dst_views="$(grep -E "^__VIEWS__ " <<<"$remote_out" | awk '{print $2}')"
  printf -- "-------------+--------------------------------+--------------------------------+------\n"
  local vm="OK"
  if [[ "$dst_views" == "NA" ]]; then
    vm="目標無 duckdb CLI（跳過 smoke）"
  elif [[ "$src_views" != "$dst_views" ]]; then
    vm="DIFF"; ok=0
  fi
  printf "%-12s | %12s %14s | %12s %14s | %s\n" "catalog_views" "$src_views" "-" "$dst_views" "-" "$vm"

  # 核心 view row-count smoke：證明目標端能透過 catalog 相對路徑「讀到」parquet
  if [[ "$dst_views" != "NA" && -f "$CATALOG" ]] && command -v duckdb >/dev/null 2>&1; then
    printf -- "-------------+--------------------------------+--------------------------------+------\n"
    local sql_out
    sql_out="$(duckdb -readonly "$CATALOG" "$SMOKE_SQL" -noheader -list 2>/dev/null || true)"
    local vv sc dc rm
    while IFS='|' read -r vv sc; do
      [[ -z "$vv" ]] && continue
      dc="$(grep -E "^__ROW__ $vv " <<<"$remote_out" | awk '{print $3}')"
      rm="OK"
      if [[ -z "$dc" || "$sc" != "$dc" ]]; then rm="DIFF"; ok=0; fi
      printf "%-12s | %12s %14s | %12s %14s | %s\n" "$vv" "$sc" "rows" "${dc:-?}" "rows" "$rm"
    done <<<"$sql_out"
  fi

  if [[ "$ok" -eq 1 ]]; then
    log "驗證 PASS：來源與目標一致（含 catalog 可讀 parquet）。"
  else
    warn "驗證發現 DIFF。若剛跑完 --apply 仍 DIFF，多半是傳輸中斷或有平行寫入；重跑一次 --apply。"
    return 1
  fi
}

# ===========================================================================
#  main
# ===========================================================================
preflight

if [[ "$VERIFY_ONLY" -eq 1 ]]; then
  do_verify
  exit $?
fi

check_duckdb_lock
[[ "$APPLY" -eq 1 ]] && checkpoint_duckdb
run_rsync
[[ "$DO_VERIFY" -eq 1 ]] && do_verify

exit 0
