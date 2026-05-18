#!/usr/bin/env bash
# Public DuckDB UI tunnel via ngrok.
#
# Required one-time setup (USER MUST DO ONCE):
#   1. Sign up at https://dashboard.ngrok.com/signup (free tier OK)
#   2. Copy authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
#      and run:
#        ngrok config add-authtoken <YOUR_AUTHTOKEN>
#   3. (For static URL) Reserve a domain at
#      https://dashboard.ngrok.com/domains  (free tier gives 1 domain)
#      Example: yourname-quantdata.ngrok-free.app
#
# Usage:
#   scripts/ngrok_tunnel.sh start [<port>]   # default port 4213
#   scripts/ngrok_tunnel.sh stop
#   scripts/ngrok_tunnel.sh status
#   scripts/ngrok_tunnel.sh url              # print the public URL
#
# Env vars (override at call site):
#   NGROK_DOMAIN     = your reserved static domain, e.g. quant.ngrok-free.app
#                      (if unset, ngrok picks an ephemeral domain)
#   NGROK_BASIC_AUTH = user:password to require basic auth (recommended!)
#   NGROK_CIDR_ALLOW = e.g. "1.2.3.0/24" to restrict source IPs

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$ROOT/catalog/.ngrok.log"
PID_FILE="$ROOT/catalog/.ngrok.pid"
POLICY_FILE="$ROOT/catalog/.ngrok_policy.yml"
PORT="${2:-4213}"

cmd="${1:-status}"

is_running() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

case "$cmd" in
    start)
        if is_running; then
            echo "ngrok already running (PID $(cat "$PID_FILE"))"
            "$0" url
            exit 0
        fi
        if ! command -v ngrok >/dev/null; then
            echo "ERROR: ngrok not in PATH; install first" >&2
            exit 1
        fi
        # Check authtoken
        if ! ngrok config check >/dev/null 2>&1; then
            echo "ERROR: ngrok not configured. Run:" >&2
            echo "  ngrok config add-authtoken <YOUR_TOKEN>" >&2
            echo "(get token at https://dashboard.ngrok.com/get-started/your-authtoken)" >&2
            exit 1
        fi
        # Build flags
        ARGS=(http "$PORT" --log=stdout)
        if [[ -n "${NGROK_DOMAIN:-}" ]]; then
            ARGS+=(--url="$NGROK_DOMAIN")
        fi
        # ngrok v3.39+ deprecated --basic-auth / --cidr-allow in favor of
        # traffic-policy. We write a one-off policy file when either is set.
        if [[ -n "${NGROK_BASIC_AUTH:-}" || -n "${NGROK_CIDR_ALLOW:-}" ]]; then
            {
                echo "on_http_request:"
                if [[ -n "${NGROK_CIDR_ALLOW:-}" ]]; then
                    echo "  - expressions:"
                    echo "      - \"!(conn.client_ip.matches('${NGROK_CIDR_ALLOW}'))\""
                    echo "    actions:"
                    echo "      - type: deny"
                fi
                if [[ -n "${NGROK_BASIC_AUTH:-}" ]]; then
                    echo "  - actions:"
                    echo "      - type: basic-auth"
                    echo "        config:"
                    echo "          credentials:"
                    echo "            - \"${NGROK_BASIC_AUTH}\""
                fi
            } > "$POLICY_FILE"
            ARGS+=(--traffic-policy-file="$POLICY_FILE")
        fi
        echo "starting: ngrok ${ARGS[*]} (log: $LOG_FILE)"
        nohup ngrok "${ARGS[@]}" >"$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        sleep 3
        if ! is_running; then
            echo "ERROR: ngrok exited; check $LOG_FILE" >&2
            tail -30 "$LOG_FILE" >&2 || true
            rm -f "$PID_FILE"
            exit 1
        fi
        echo "ngrok PID $(cat "$PID_FILE")"
        "$0" url
        ;;
    stop)
        if ! is_running; then
            echo "ngrok not running"
            rm -f "$PID_FILE"
            exit 0
        fi
        kill "$(cat "$PID_FILE")"
        for _ in 1 2 3 4 5; do
            is_running || break
            sleep 1
        done
        kill -9 "$(cat "$PID_FILE")" 2>/dev/null || true
        rm -f "$PID_FILE" "$POLICY_FILE"
        echo "stopped"
        ;;
    status)
        if is_running; then
            echo "ngrok running (PID $(cat "$PID_FILE"))"
            "$0" url
        else
            echo "ngrok not running"
        fi
        ;;
    url)
        # ngrok exposes its API on localhost:4040
        if command -v curl >/dev/null; then
            url=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
                  | grep -oE '"public_url":"[^"]+"' | head -1 \
                  | sed 's/"public_url":"//;s/"$//')
            if [[ -n "$url" ]]; then
                echo "public URL: $url"
            else
                echo "(no tunnel URL yet — ngrok may still be starting; check $LOG_FILE)"
            fi
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|status|url} [port]" >&2
        exit 2
        ;;
esac
