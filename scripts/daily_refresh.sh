#!/usr/bin/env bash
# daily_refresh.sh — pull latest TEJ data + ingest to silver + rebuild catalog.
#
# Designed to be invoked from cron / systemd-timer. Idempotent: re-running on
# the same day (or after a partial failure) does NOT double-write thanks to
# fetch_tej.py's --mode merge and qd_ingest's manifest dedup.
#
# Behaviour:
#   1. flock prevents concurrent runs (no-op if another instance still running)
#   2. Auto-sources TEJAPI_KEY/BASE from fish universal vars if unset
#   3. fetch_tej --table all --append-since-silver  → CSV+silver-parquet
#   4. qd-ingest tej-{stock,inst-stock,margin} → silver bars/flows
#   5. qd-ingest build-catalog (staging swap if UI lock held)
#   6. All output appended to meta/audit/daily_refresh_<YYYY-MM-DD>.log
#
# Exit codes: 0 ok, 1 fetch failed, 2 ingest failed, 3 catalog failed,
#             10 locked (another instance running), 11 missing TEJAPI_KEY.

set -u
set -o pipefail

REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
cd "$REPO"

LOG_DIR="$REPO/meta/audit"
TODAY="$(date +%Y-%m-%d)"
LOG="$LOG_DIR/daily_refresh_${TODAY}.log"
mkdir -p "$LOG_DIR"

LOCK="/tmp/quantdata_daily_refresh.lock"

log() {
    # ISO8601 + level + message, also to stderr if we're interactive
    local lvl="$1"; shift
    printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$lvl" "$*" | tee -a "$LOG" >&2
}

trap 'log ERROR "interrupted (signal)"; exit 130' INT TERM

# ---- 1. flock -------------------------------------------------------------
exec 9>"$LOCK" || { echo "cannot open lock file $LOCK" >&2; exit 10; }
if ! flock -n 9; then
    echo "another daily_refresh is running (lock: $LOCK) — exit" >&2
    exit 10
fi

log INFO "==== daily_refresh start (repo=$REPO) ===="

# ---- 2. Env from fish universal vars (cron-safe) -------------------------
if [[ -z "${TEJAPI_KEY:-}" ]]; then
    FISH_VARS="$HOME/.config/fish/fish_variables"
    if [[ -r "$FISH_VARS" ]]; then
        # SETUVAR --export TEJAPI_KEY:hwVU...
        # fish escapes ASCII < 0x20 / non-printables as \xHH. The values we
        # care about (key, https URL) only need \x3a (:) and \x2e (.) decoded.
        decode_fish() {
            python3 -c '
import sys, re
line = sys.stdin.read().strip()
m = re.search(r":([^:]*)$", line)
if not m: sys.exit(0)
val = m.group(1)
val = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1),16)), val)
print(val)
'
        }
        K=$(grep '^SETUVAR --export TEJAPI_KEY:' "$FISH_VARS" 2>/dev/null | decode_fish || true)
        B=$(grep '^SETUVAR --export TEJAPI_BASE:' "$FISH_VARS" 2>/dev/null | decode_fish || true)
        if [[ -n "$K" ]]; then
            export TEJAPI_KEY="$K"
            export TEJAPI_BASE="${B:-https://api.tej.com.tw}"
            log INFO "sourced TEJAPI_KEY from fish_variables"
        fi
    fi
fi

if [[ -z "${TEJAPI_KEY:-}" ]]; then
    log ERROR "TEJAPI_KEY not set and not found in fish_variables — abort"
    exit 11
fi
export TEJAPI_BASE="${TEJAPI_BASE:-https://api.tej.com.tw}"

VENV_PY="$REPO/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    log ERROR ".venv/bin/python not executable at $VENV_PY — abort"
    exit 11
fi

# ---- 3. Fetch -------------------------------------------------------------
log INFO "step 1/3: fetch_tej --table all --append-since-silver --mode merge"
if ! "$VENV_PY" "$REPO/scripts/fetch_tej.py" \
        --table all --append-since-silver --mode merge \
        >> "$LOG" 2>&1; then
    log ERROR "fetch_tej.py failed (see $LOG)"
    exit 1
fi
log INFO "step 1/3 done"

# ---- 4. Ingest CSV-backed tables ----------------------------------------
RAW_DIR="${QUANTDATA_RAW:-$REPO/../RAW_SOURCES}/TEJ資料"

ingest_one() {
    local cmd="$1" csv_name="$2"
    local csv="$RAW_DIR/$csv_name"
    if [[ ! -r "$csv" ]]; then
        log WARN "skip $cmd: $csv missing (fetch may have skipped this table)"
        return 0
    fi
    log INFO "ingest: qd-ingest $cmd --csv $csv"
    if ! "$VENV_PY" -m qd_ingest.cli "$cmd" --csv "$csv" >> "$LOG" 2>&1; then
        log ERROR "qd-ingest $cmd failed"
        return 1
    fi
    return 0
}

ingest_one "tej-stock"      "TWN_EWPRCD_股價.csv"      || exit 2
ingest_one "tej-inst-stock" "TWN_EWTINST1_三大法人.csv" || exit 2
ingest_one "tej-margin"     "TWN_EWGIN_融資融券.csv"    || exit 2
log INFO "step 2/3 done"

# ---- 5. Catalog rebuild --------------------------------------------------
# fetch_tej.py also writes silver parquet directly for futures / P0-P2 tables,
# so we always rebuild even if no CSV was touched.
CATALOG="$REPO/catalog/quant.duckdb"
STAGING="$REPO/catalog/quant_refresh.duckdb"

# Check if any process holds a write lock on the live catalog
LOCK_HELD=""
if command -v fuser >/dev/null 2>&1; then
    if fuser "$CATALOG" >/dev/null 2>&1; then
        LOCK_HELD="yes"
    fi
fi

if [[ -n "$LOCK_HELD" ]]; then
    log WARN "catalog locked by another process — building to staging $STAGING"
    if ! "$VENV_PY" -m qd_ingest.cli build-catalog --db-path "$STAGING" >> "$LOG" 2>&1; then
        log ERROR "build-catalog (staging) failed"
        exit 3
    fi
    log WARN "staging catalog ready: $STAGING. Swap manually after releasing UI lock:"
    log WARN "  kill <duckdb-ui-pid>; mv $CATALOG $CATALOG.bak; mv $STAGING $CATALOG"
else
    log INFO "step 3/3: build-catalog (in-place)"
    # Build to staging first, then swap atomically — survives crashes mid-write
    if ! "$VENV_PY" -m qd_ingest.cli build-catalog --db-path "$STAGING" >> "$LOG" 2>&1; then
        log ERROR "build-catalog failed"
        exit 3
    fi
    if [[ -f "$CATALOG" ]]; then
        mv "$CATALOG" "$CATALOG.prev"
    fi
    mv "$STAGING" "$CATALOG"
    rm -f "$CATALOG.prev"
    log INFO "catalog swapped: $CATALOG"
fi

log INFO "==== daily_refresh OK ===="
exit 0
