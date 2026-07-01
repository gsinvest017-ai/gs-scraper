#!/usr/bin/env bash
# QUANTDATA 一鍵跨主機 migrate。
#
# 把整個 repo（程式碼 + .git + 18G 資料湖 bronze/silver/gold + DuckDB catalog）
# idempotent 鏡像到目標主機。DuckDB catalog 的 view 全用相對路徑
# (read_parquet('silver/...'))，所以目標端不需改任何 SQL，repo 樹一致即可開。
#
# 依目標 OS 走兩條傳輸路徑（--os-type 決定）：
#   - linux / wsl ：Approach A — rsync-over-SSH 鏡像到 SSH 可達的 Linux 主機。
#   - windows     ：robocopy over SMB 鏡像到內網 Windows 主機（無需 sshd/rsync）。
#                   此分支需在「能呼叫 robocopy.exe 的環境」執行——在來源 Windows
#                   用 Git Bash 服務 dashboard（run.ps1 ui）即滿足。
#
# Usage:
#   scripts/migrate_to_host.sh [options]            # DRY-RUN 預覽（預設，不動目標）
#   scripts/migrate_to_host.sh --apply              # 真的傳輸（Linux 目標）
#   scripts/migrate_to_host.sh --apply --verify     # 傳輸後做來源 vs 目標驗證
#   scripts/migrate_to_host.sh --verify-only        # 只比對，不傳輸
#   scripts/migrate_to_host.sh --os-type windows --host user@HOST --apply   # Windows 目標(robocopy/SMB)
#
# 目標主機設定（優先序）：
#   1. CLI flag：   --host user@host   --path /remote/path   --port 22
#   2. config 檔：  scripts/migrate.conf （gitignored；見 migrate.conf.example）
#   3. 環境變數：   MIGRATE_HOST / MIGRATE_PATH / MIGRATE_SSH_PORT
#
# 選項：
#   --os-type T       目標 OS：linux|wsl|windows（預設 linux）。windows=robocopy/SMB
#   --host U@H        目標目的地（user@host）；windows 時 user 用於 SMB 認證
#   --path P          目標端 repo 路徑（預設沿用來源端路徑；windows 接受 X:\ 或 \\UNC）
#   --port N          ssh port（預設 22；windows 分支忽略）
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
OS_TYPE="linux"

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
    --os-type)     OS_TYPE="$2"; shift 2 ;;
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

case "$OS_TYPE" in
  linux|wsl|windows) ;;
  *) fail "未知 --os-type：$OS_TYPE（只接受 linux|wsl|windows）" ;;
esac

# 目標端路徑預設沿用來源端絕對路徑（同 layout，relative-path view 直接可用）。
# windows 分支不在這裡套預設（空字串 → 由來源磁碟推管理共享），故僅非 windows 套用。
[[ -z "$TARGET_PATH" && "$OS_TYPE" != "windows" ]] && TARGET_PATH="$ROOT"

# ---- 共用：ssh / 目的地字串 ---------------------------------------------
# 認證模式：
#   - 預設 key-only（BatchMode=yes，不會跳密碼提示，CI / cron 安全）。
#   - 若環境變數 SSHPASS 有值（dashboard 走這條）→ 用 sshpass -e 帶密碼，
#     並關掉 BatchMode、改 accept-new 讓首次連線的目標 host key 不卡關。
#   密碼只經 SSHPASS env 傳遞，絕不出現在指令列 / log。
SSHPASS_PREFIX=()
if [[ -n "${SSHPASS:-}" ]]; then
  command -v sshpass >/dev/null 2>&1 \
    || fail "需要 password 認證但本機沒裝 sshpass。請 'sudo apt install sshpass'（或用 ssh key 改走免密）。"
  SSHPASS_PREFIX=(sshpass -e)
  SSH_OPTS=(-p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
else
  SSH_OPTS=(-p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=10)
fi
SSH_CMD=("${SSHPASS_PREFIX[@]}" ssh "${SSH_OPTS[@]}")
# 給 rsync -e 用的字串（含 port 與 host-key 策略，但不含 sshpass —— sshpass 包在 rsync 外層）
RSYNC_SSH="ssh ${SSH_OPTS[*]}"
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
  if ! "${SSH_CMD[@]}" "$HOST" true 2>/dev/null; then
    if [[ -n "${SSHPASS:-}" ]]; then
      fail "SSH 連不上 $HOST（password 認證）。確認 IP/port/帳號/密碼正確、目標 sshd 開放。"
    else
      fail "SSH 連不上 $HOST。先設好 ssh key（ssh -p $SSH_PORT $HOST true），或設 SSHPASS env 走密碼。"
    fi
  fi
  log "SSH OK（$([[ -n "${SSHPASS:-}" ]] && echo password || echo key)）"
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

  "${SSHPASS_PREFIX[@]}" rsync "${opts[@]}" "${EXCLUDES[@]}" \
    -e "$RSYNC_SSH" \
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
#  Windows 目標分支（robocopy over SMB）
#  -------------------------------------------------------------------------
#  目標是一台內網 Windows 主機（沒有 sshd / rsync），改用 Windows 原生 robocopy
#  透過 SMB（具名共享或管理共享 C$）鏡像。需在能呼叫 robocopy.exe 的環境執行——
#  在來源 Windows 用 Git Bash 服務 dashboard（run.ps1 ui）即滿足（PATH 含 system32）。
#
#  認證：
#    - 未帶密碼 → 用「執行 dashboard 的 Windows 帳號」現有工作階段認證（同帳號/
#      同網域即免密）。
#    - 帶密碼（GUI 密碼欄 → SSHPASS env）→ 先 net use \\HOST\IPC$ 建立認證工作階段，
#      收尾再 net use /delete。
#  robocopy 不會重建 venv / catalog；搬完需在目標端 run.ps1 setup + build-catalog
#  （收尾會印出指令）。
# ===========================================================================
WIN_NET_AUTHED=0
WIN_IPC=""

win_cleanup() {
  if [[ "$WIN_NET_AUTHED" -eq 1 && -n "$WIN_IPC" ]]; then
    net.exe use "$WIN_IPC" /delete >/dev/null 2>&1 || true
    WIN_NET_AUTHED=0
  fi
}

# 解析目標 UNC：
#   空      → 由來源磁碟推管理共享 \\HOST\<drive>$\<rest>
#   X:\path → \\HOST\X$\path（管理共享）
#   \\...   → 原樣（使用者自填的 UNC / 具名共享）
win_resolve_dest() {
  local host="$1" src_win="$2" tp="$3" drive rest
  if [[ -z "$tp" ]]; then
    drive="${src_win:0:1}"; rest="${src_win:2}"
    printf '\\\\%s\\%s$%s' "$host" "$drive" "$rest"
  elif [[ "$tp" == '\\'* ]]; then
    printf '%s' "$tp"
  elif [[ "$tp" =~ ^[A-Za-z]:\\ ]]; then
    drive="${tp:0:1}"; rest="${tp:2}"
    printf '\\\\%s\\%s$%s' "$host" "$drive" "$rest"
  else
    printf '\\\\%s\\%s' "$host" "$tp"
  fi
}

windows_verify() {
  local dest_unc="$1" dest_bash
  # UNC → Git Bash 走訪路徑：\\HOST\C$\path → //HOST/C$/path
  dest_bash="$(printf '%s' "$dest_unc" | sed 's#^\\\\#//#; s#\\#/#g')"
  log "=== 驗證（Windows）：來源 vs 目標 per-layer 檔數 / 位元組 ==="
  printf "%-12s | %10s %14s | %10s %14s | %s\n" "layer" "src_files" "src_bytes" "dst_files" "dst_bytes" "match"
  printf -- "-------------+-----------------------------+-----------------------------+------\n"
  local ok=1 d sn sb dn db m
  for d in "${LAYERS[@]}"; do
    if [[ -d "$ROOT/$d" ]]; then
      sn="$(find "$ROOT/$d" -type f 2>/dev/null | wc -l | tr -d ' ')"
      sb="$(du -sb "$ROOT/$d" 2>/dev/null | cut -f1)"
    else sn=0; sb=0; fi
    if [[ -d "$dest_bash/$d" ]]; then
      dn="$(find "$dest_bash/$d" -type f 2>/dev/null | wc -l | tr -d ' ')"
      db="$(du -sb "$dest_bash/$d" 2>/dev/null | cut -f1)"
    else dn=0; db=0; fi
    m="OK"; [[ "$sn" != "$dn" || "$sb" != "$db" ]] && { m="DIFF"; ok=0; }
    printf "%-12s | %10s %14s | %10s %14s | %s\n" "$d" "$sn" "$sb" "$dn" "$db" "$m"
  done
  local cm="OK"
  [[ -f "$dest_bash/$CATALOG" ]] || { cm="缺 catalog 檔"; ok=0; }
  printf "%-12s | %10s %14s | %10s %14s | %s\n" "catalog" "-" "-" "-" "-" "$cm"
  if [[ "$ok" -eq 1 ]]; then
    log "驗證 PASS：來源與目標 per-layer 檔數/位元組一致。"
    return 0
  fi
  warn "驗證發現 DIFF；若剛跑完 apply 仍 DIFF，多半是傳輸中斷或有平行寫入，重跑一次即可收斂。"
  return 1
}

windows_main() {
  # Git Bash/MSYS 會把看起來像路徑的引數（如 robocopy 的 /MIR、net 的 /delete）
  # 改寫成 'C:\Program Files\Git\MIR' 之類，導致原生 .exe 收到錯誤參數。對本分支內
  # 所有原生程式（robocopy.exe / net.exe / duckdb）關閉這個自動轉換。
  export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

  command -v robocopy.exe >/dev/null 2>&1 || fail "找不到 robocopy.exe（Windows 分支需在能呼叫 robocopy 的環境執行，例如來源 Windows 的 Git Bash）。"
  command -v cygpath      >/dev/null 2>&1 || fail "找不到 cygpath（需在 Git Bash/MSYS 環境執行 Windows 分支）。"
  command -v net.exe      >/dev/null 2>&1 || fail "找不到 net.exe。"
  [[ -n "$HOST" ]] || fail "未指定目標主機。用 --host user@host（user 用於 SMB 認證）。"

  local win_user win_host src_win dest_unc
  win_user="${HOST%@*}"; win_host="${HOST##*@}"
  [[ "$win_user" == "$win_host" ]] && win_user=""   # 沒有 @ → 用現有工作階段
  src_win="$(cygpath -w "$ROOT")"
  dest_unc="$(win_resolve_dest "$win_host" "$src_win" "$TARGET_PATH")"

  log "OS 目標：Windows（robocopy/SMB）"
  log "來源：$src_win"
  log "目標：$dest_unc"
  if [[ "$VERIFY_ONLY" -eq 1 ]]; then log "模式：VERIFY-ONLY（只比對）"
  elif [[ "$APPLY" -eq 1 ]]; then log "模式：APPLY（真的搬）"
  else log "模式：DRY-RUN（robocopy /L，僅列不寫）"; fi
  [[ -n "$BWLIMIT" ]] && warn "robocopy 不支援 KB/s 限速，--bwlimit 在 Windows 分支忽略。"

  # SMB 認證（IPC$）
  WIN_IPC="$(printf '\\\\%s\\IPC$' "$win_host")"
  trap win_cleanup EXIT
  if [[ -n "${SSHPASS:-}" ]]; then
    [[ -n "$win_user" ]] || fail "有帶密碼但沒給 SMB 帳號，請在 --host 用 user@host。"
    log "SMB 認證：net use $WIN_IPC /user:$win_user（密碼經 env，不顯示）"
    if ! net.exe use "$WIN_IPC" "$SSHPASS" /user:"$win_user" >/dev/null 2>&1; then
      fail "SMB 認證失敗（net use $WIN_IPC）。確認帳號/密碼、目標開啟「檔案及印表機共享」、防火牆放行 445。"
    fi
    WIN_NET_AUTHED=1
  else
    log "SMB 認證：用目前 Windows 工作階段帳號（未帶密碼）。"
  fi

  if [[ "$VERIFY_ONLY" -eq 1 ]]; then
    windows_verify "$dest_unc"; local vrc=$?
    win_cleanup; trap - EXIT
    return $vrc
  fi

  # DuckDB CHECKPOINT（best-effort；*.duckdb.wal 已排除不搬）
  if [[ "$APPLY" -eq 1 ]]; then
    if command -v duckdb >/dev/null 2>&1; then
      log "CHECKPOINT $CATALOG（落盤 WAL）…"
      timeout 60 duckdb "$CATALOG" "CHECKPOINT;" >/dev/null 2>&1 \
        || warn "CHECKPOINT 失敗（catalog 可能被鎖）；確認沒有 run.ps1 ui / duckdb -ui 開著。"
    else
      warn "找不到 duckdb CLI，跳過 CHECKPOINT；請先關掉開著 catalog 的程式再搬。"
    fi
  fi

  # robocopy 參數（對齊 rsync EXCLUDES）
  local rc=( "$src_win" "$dest_unc" )
  if [[ "$DO_DELETE" -eq 1 ]]; then rc+=(/MIR); else rc+=(/E); fi
  rc+=( /XD .venv venv site __pycache__ .pytest_cache .mypy_cache .ruff_cache tmp _staging
        "$src_win\\.claude\\local"
        /XF "*.pyc" "*.duckdb.wal" "*.duckdb.tmp" "*.duckdb.bak*"
        "$src_win\\catalog\\.ngrok.log" "$src_win\\catalog\\.duckdb_public_ui.log"
        /R:2 /W:5 /MT:16 /NP /NDL )
  [[ "$APPLY" -eq 0 ]] && rc+=(/L)

  log "robocopy ${rc[*]}"
  if [[ "$APPLY" -eq 0 ]]; then log "=== DRY-RUN（robocopy /L，不會改目標）==="
  else log "=== APPLY：robocopy 鏡像到 $dest_unc ==="; fi

  set +e
  robocopy.exe "${rc[@]}"
  local code=$?
  set -e
  # robocopy exit code 是 bitmask：0-7 成功；bit8(>=8) 有檔失敗；bit16(>=16) 嚴重錯誤
  if   [[ "$code" -ge 16 ]]; then fail "robocopy 嚴重錯誤（exit=$code）— 目標不可達 / 權限 / 共享未開。"
  elif [[ "$code" -ge 8  ]]; then fail "robocopy 有檔案複製失敗（exit=$code，見上方輸出）。"
  else log "robocopy 完成（exit=$code；bit1=已複製 bit2=多餘已刪 bit4=不符）。"; fi

  if [[ "$APPLY" -eq 0 ]]; then
    log "DRY-RUN 結束。確認上面清單後，勾「確認執行」再跑。"
    win_cleanup; trap - EXIT
    return 0
  fi

  [[ "$DO_VERIFY" -eq 1 ]] && windows_verify "$dest_unc"

  win_cleanup; trap - EXIT
  log "搬檔完成 ✅。目標端收尾（在目標 Windows 上執行）："
  log "    cd <目標 repo> ; .\\run.ps1 setup        # 重建 .venv + 安裝套件"
  log "    .\\.venv\\Scripts\\python.exe -m qd_ingest.cli build-catalog   # 重生 catalog"
  log "    Copy-Item -Force scripts\\git-hooks\\commit-msg .git\\hooks\\commit-msg"
  log "    .\\run.ps1 ui   →  http://127.0.0.1:5050/"
  log "驗收通過前，來源端資料先別刪。"
}

# ===========================================================================
#  main
# ===========================================================================
if [[ "$OS_TYPE" == "windows" ]]; then
  windows_main
  exit $?
fi

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
