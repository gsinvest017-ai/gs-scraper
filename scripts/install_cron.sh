#!/usr/bin/env bash
# install_cron.sh — idempotent installer for the QUANTDATA daily refresh cron entry.
#
# Adds a marked block to the user's crontab that runs scripts/daily_refresh.sh
# every weekday at 17:30 CST (台股 13:30 收盤 + 4h 給 TEJ EOD 落地).
#
# Usage:
#   scripts/install_cron.sh              # install / replace block
#   scripts/install_cron.sh --uninstall  # remove block
#   scripts/install_cron.sh --show       # print block that would be installed
#   scripts/install_cron.sh --hour HH --minute MM   # override schedule
#
# Idempotent: re-running replaces only the QUANTDATA block, leaves other
# crontab entries (e.g. gs-claude-config night-shift) untouched.

set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
REFRESH_SCRIPT="$REPO/scripts/daily_refresh.sh"

BEGIN_MARKER="# >>> quantdata-daily-refresh <<<"
END_MARKER="# <<< quantdata-daily-refresh >>>"

# Default schedule: 17:30 CST every weekday (Mon-Fri).
# Cron runs in system local time (Asia/Taipei). Sat/Sun TEJ data does not
# update, so we save TEJ a call.
HOUR="17"
MINUTE="30"
ACTION="install"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall) ACTION="uninstall"; shift ;;
        --show)      ACTION="show"; shift ;;
        --hour)      HOUR="$2"; shift 2 ;;
        --minute)    MINUTE="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 64 ;;
    esac
done

if [[ ! -x "$REFRESH_SCRIPT" ]]; then
    echo "ERROR: $REFRESH_SCRIPT not executable" >&2
    exit 1
fi

build_block() {
    cat <<EOF
$BEGIN_MARKER
# Refreshes TEJ data + ingests to silver + rebuilds catalog every weekday.
# Logs: $REPO/meta/audit/daily_refresh_YYYY-MM-DD.log
# Uninstall: $REPO/scripts/install_cron.sh --uninstall
$MINUTE $HOUR * * 1-5 $REFRESH_SCRIPT >> $REPO/meta/audit/daily_refresh_cron.log 2>&1
$END_MARKER
EOF
}

current_crontab() {
    crontab -l 2>/dev/null || true
}

strip_block() {
    # Remove existing block between markers (and the markers themselves).
    # awk so it works on BSD/GNU consistently.
    awk -v b="$BEGIN_MARKER" -v e="$END_MARKER" '
        $0 == b { skip=1; next }
        $0 == e { skip=0; next }
        !skip { print }
    '
}

case "$ACTION" in
    show)
        build_block
        exit 0
        ;;
    uninstall)
        new=$(current_crontab | strip_block)
        if [[ -z "${new//[[:space:]]/}" ]]; then
            crontab -r 2>/dev/null || true
        else
            printf '%s\n' "$new" | crontab -
        fi
        echo "[uninstall] removed quantdata-daily-refresh block"
        ;;
    install)
        existing=$(current_crontab | strip_block)
        block=$(build_block)
        {
            if [[ -n "${existing//[[:space:]]/}" ]]; then
                printf '%s\n' "$existing"
                # ensure separator newline
                if [[ "$(printf '%s' "$existing" | tail -c1)" != $'\n' ]]; then
                    echo
                fi
            fi
            printf '%s\n' "$block"
        } | crontab -
        echo "[install] crontab now contains:"
        echo "----------------------------------------"
        crontab -l
        echo "----------------------------------------"
        echo "Schedule: $MINUTE $HOUR * * 1-5  (台股交易日 ${HOUR}:${MINUTE} CST)"
        echo "Uninstall: $0 --uninstall"
        ;;
esac
