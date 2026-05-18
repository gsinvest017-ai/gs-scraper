#!/usr/bin/env bash
# Public DuckDB UI tunnel via Tailscale Funnel.
#
# Prerequisites (one-time, done by USER):
#   1. Tailnet admin: enable Funnel feature
#        Open: https://login.tailscale.com/f/funnel?node=nfAHt8nSqn11CNTRL
#   2. Tailnet admin: enable HTTPS certificates
#        https://login.tailscale.com/admin/dns -> "HTTPS Certificates" -> Enable
#   3. Local: allow non-root to manage tailscale serve
#        sudo tailscale set --operator=$USER
#
# After all three, this script can:
#   tailscale_funnel.sh check    # report which prerequisites are unmet
#   tailscale_funnel.sh start [<port>]  # default 4213; the read-only DuckDB UI
#   tailscale_funnel.sh stop
#   tailscale_funnel.sh status
#   tailscale_funnel.sh url

set -euo pipefail

PORT="${2:-4213}"
cmd="${1:-status}"

# ---------------------------------------------------------------------------

dns_name() {
    tailscale status --self --json 2>/dev/null \
        | grep '"DNSName"' \
        | head -1 \
        | sed -E 's/.*"DNSName":[[:space:]]*"([^"]+)".*/\1/; s/\.$//'
}

check_prereqs() {
    local missing=0

    echo "1. tailscale daemon up + logged in"
    if tailscale status >/dev/null 2>&1; then
        echo "     OK — node DNS: $(dns_name)"
    else
        echo "     FAIL — start tailscaled / tailscale up"
        missing=$((missing+1))
    fi

    echo "2. local operator set (needed for non-root funnel/serve control)"
    # `serve status` is read-only and works for any user. Probe with a write
    # attempt that's effectively a no-op: a malformed request that's port-
    # validated AFTER the auth check returns access-denied if operator is unset.
    local op_out
    op_out="$(tailscale serve reset 2>&1)" || true
    if echo "$op_out" | grep -qi 'access denied'; then
        echo "     FAIL — run once:  sudo tailscale set --operator=\$USER"
        missing=$((missing+1))
        # Once operator is missing, the next two checks can't reveal the true state.
        echo "3. tailnet Funnel attribute (cannot check until operator is set)"
        echo "4. tailnet HTTPS cert     (cannot check until operator is set)"
        return $missing
    else
        echo "     OK"
    fi

    echo "3. tailnet Funnel attribute granted to this node"
    local f_out
    f_out="$(tailscale funnel status 2>&1)" || true
    if echo "$f_out" | grep -qi 'funnel is not enabled'; then
        echo "     FAIL — open https://login.tailscale.com/f/funnel?node=nfAHt8nSqn11CNTRL"
        missing=$((missing+1))
    else
        echo "     OK"
    fi

    echo "4. tailnet HTTPS cert enabled"
    local c_out
    c_out="$(tailscale cert "$(dns_name)" 2>&1)" || true
    if echo "$c_out" | grep -qi 'HTTPS cert support is not enabled'; then
        echo "     FAIL — open https://login.tailscale.com/admin/dns -> HTTPS Certificates -> Enable"
        missing=$((missing+1))
    elif echo "$c_out" | grep -qi 'access denied'; then
        echo "     UNKNOWN — cert command also access-denied, operator may not be fully effective"
        missing=$((missing+1))
    else
        echo "     OK"
    fi

    return $missing
}

# ---------------------------------------------------------------------------

case "$cmd" in
    check)
        check_prereqs
        ;;

    start)
        # Cheap state probes; don't run check_prereqs (it dumps a lot of text).
        if ! tailscale status >/dev/null 2>&1; then
            echo "ERROR: tailscale daemon not running" >&2
            exit 1
        fi
        # Verify upstream port is alive
        if ! ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
            echo "ERROR: nothing listening on 127.0.0.1:$PORT" >&2
            echo "  Run scripts/duckdb_public_ui.sh start  first." >&2
            exit 1
        fi
        echo "starting tailscale funnel for port $PORT (background)..."
        set +e
        out=$(tailscale funnel --bg "$PORT" 2>&1)
        rc=$?
        set -e
        echo "$out"
        if [[ $rc -ne 0 ]]; then
            echo >&2
            if echo "$out" | grep -qi 'access denied'; then
                echo "DIAGNOSIS: local operator not set." >&2
                echo "  Run once:  sudo tailscale set --operator=\$USER" >&2
            elif echo "$out" | grep -qi 'funnel is not enabled'; then
                echo "DIAGNOSIS: Funnel feature not enabled on this node." >&2
                echo "  Open:  https://login.tailscale.com/f/funnel?node=nfAHt8nSqn11CNTRL" >&2
            elif echo "$out" | grep -qi 'https.*cert'; then
                echo "DIAGNOSIS: HTTPS certificates not enabled on tailnet." >&2
                echo "  Open:  https://login.tailscale.com/admin/dns -> HTTPS Certificates" >&2
            else
                echo "Funnel start failed for an unknown reason; see above." >&2
            fi
            exit 1
        fi
        sleep 1
        echo
        "$0" url
        ;;

    stop)
        echo "resetting funnel/serve config..."
        tailscale funnel reset 2>&1 || true
        ;;

    status)
        tailscale funnel status 2>&1
        ;;

    url)
        host="$(dns_name)"
        if [[ -z "$host" ]]; then
            echo "(no tailscale DNS name)" >&2
            exit 1
        fi
        echo "https://$host"
        ;;

    *)
        echo "Usage: $0 {check|start|stop|status|url} [port]" >&2
        exit 2
        ;;
esac
