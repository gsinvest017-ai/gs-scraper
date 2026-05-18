#!/usr/bin/env bash
# Manage a read-only DuckDB UI session against a snapshot of the catalog,
# for exposure through ngrok / Tailscale Funnel.
#
# IMPORTANT: DuckDB's UI extension is a per-machine singleton — only one
# `duckdb -ui` process can hold the UI at a time. This script therefore:
#   * 'start'  — refuses if any other duckdb -ui is already running,
#                otherwise snapshots the catalog and launches a read-only UI
#   * 'replace' — stops the existing duckdb -ui (if any) and replaces it
#                 with our read-only snapshot UI
#   * 'stop'   — shuts our read-only UI down
#   * 'status' — what's running, on what port
#   * 'refresh' — re-snapshot from live catalog (requires 'stop' + 'start'
#                 to pick up since DuckDB caches the file)
#
# Why use a snapshot rather than exposing the live catalog directly:
#   - Anyone who reaches the URL has full SQL access. read-only mode bars
#     INSERT/UPDATE/DELETE/DROP, but live catalog could still be corrupted
#     by attached writes; a snapshot makes the blast radius local.
#   - Snapshots also let `tailscale funnel` / `ngrok` keep serving even
#     while you re-ingest data into the live catalog.
#
# After 'start' / 'replace', the UI listens on the port DuckDB picks
# (usually 4213 if free, else 4214+). Tunnel with:
#   tailscale funnel <port>
#   ngrok http --url=<your-static-domain> <port>

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_DB="$ROOT/catalog/quant.duckdb"
PUBLIC_DB="$ROOT/catalog/quant_public.duckdb"
PORT="${DUCKDB_PUBLIC_PORT:-4214}"
PID_FILE="$ROOT/catalog/.duckdb_public_ui.pid"
LOG_FILE="$ROOT/catalog/.duckdb_public_ui.log"

cmd="${1:-status}"

snapshot() {
    if [[ ! -f "$LIVE_DB" ]]; then
        echo "ERROR: live catalog not found at $LIVE_DB" >&2
        exit 1
    fi
    cp "$LIVE_DB" "$PUBLIC_DB.tmp"
    mv "$PUBLIC_DB.tmp" "$PUBLIC_DB"
    echo "snapshot refreshed: $PUBLIC_DB ($(stat -c %s "$PUBLIC_DB") bytes)"
}

is_running() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

launch() {
    snapshot
    echo "launching duckdb -readonly -ui against snapshot (log: $LOG_FILE)..."
    # cd into ROOT so view definitions resolve relative parquet paths.
    cd "$ROOT"
    # The duckdb -ui CLI reads stdin and exits on EOF (so the UI server dies).
    # Feed it `tail -f /dev/null` so its stdin never closes; backgrounded so
    # the script returns. We capture the duckdb PID (not the tail PID).
    setsid bash -c "tail -f /dev/null | duckdb -readonly -ui '$PUBLIC_DB'" \
        >"$LOG_FILE" 2>&1 &
    sleep 2
    # find the duckdb child
    local duck_pid
    duck_pid="$(pgrep -f "duckdb -readonly -ui $PUBLIC_DB" | head -1)"
    if [[ -z "$duck_pid" ]]; then
        echo "ERROR: duckdb -ui did not start; check $LOG_FILE" >&2
        tail -20 "$LOG_FILE" >&2 || true
        return 1
    fi
    echo "$duck_pid" > "$PID_FILE"
    sleep 1
    local actual_port
    actual_port="$(ss -tlnp 2>/dev/null | awk -v pid="$duck_pid" '$0 ~ "pid="pid {print $4}' | sed 's/.*://' | head -1)"
    echo "public UI PID $duck_pid, listening on 127.0.0.1:${actual_port:-?}"
    echo
    echo "Tunnel it with one of:"
    echo "  tailscale funnel ${actual_port:-<port>}        # tailnet-managed public URL"
    echo "  ngrok http --url=<your-static-domain> ${actual_port:-<port>}  # ngrok"
}

other_ui_pid() {
    pgrep -f 'duckdb .*-ui' | grep -v "^$(cat "$PID_FILE" 2>/dev/null || echo NONE)\$" | head -1 || true
}

case "$cmd" in
    start)
        if is_running; then
            echo "our public UI already running (PID $(cat "$PID_FILE"))"
            exit 0
        fi
        other_pid=$(other_ui_pid)
        if [[ -n "$other_pid" ]]; then
            echo "ERROR: another duckdb -ui is running (PID $other_pid)." >&2
            echo "  Stop it first or use '$0 replace' to take over." >&2
            exit 1
        fi
        launch
        ;;
    replace)
        if is_running; then
            echo "our public UI already running (PID $(cat "$PID_FILE"))"
            exit 0
        fi
        other_pid=$(other_ui_pid)
        if [[ -n "$other_pid" ]]; then
            echo "stopping existing duckdb -ui PID $other_pid..."
            kill "$other_pid"
            for _ in 1 2 3 4 5; do
                kill -0 "$other_pid" 2>/dev/null || break
                sleep 1
            done
            kill -9 "$other_pid" 2>/dev/null || true
        fi
        launch
        ;;
    stop)
        if ! is_running; then
            echo "not running"
            rm -f "$PID_FILE"
            exit 0
        fi
        pid="$(cat "$PID_FILE")"
        echo "stopping public UI PID $pid"
        kill "$pid"
        for _ in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$pid" 2>/dev/null || true
        rm -f "$PID_FILE"
        ;;
    status)
        if is_running; then
            echo "public UI running (PID $(cat "$PID_FILE"))"
            echo "snapshot: $PUBLIC_DB ($(stat -c %s "$PUBLIC_DB" 2>/dev/null || echo '?') bytes)"
            ss -tlnp 2>/dev/null | grep duckdb || true
        else
            echo "public UI not running"
        fi
        ;;
    refresh)
        snapshot
        echo "NOTE: open clients must reconnect / refresh tab to see updated data"
        ;;
    *)
        echo "Usage: $0 {start|replace|stop|status|refresh}" >&2
        exit 2
        ;;
esac
