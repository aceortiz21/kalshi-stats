#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.venv/bin/python"

DB="data/kalshi_stats_snapshot.sqlite"
SCENARIOS="config/scenarios.json"
DASHBOARD="reports/dashboard.html"

URL="http://127.0.0.1:8000/reports/dashboard.html"

SERVER_PID=""
BTC_SUPERVISOR_PID=""
SYNC_SUPERVISOR_PID=""

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: .venv Python not found."
    echo "Expected: $PYTHON"
    exit 1
fi

if [[ ! -f ".env" ]]; then
    echo "ERROR: ~/stats/.env was not found."
    exit 1
fi

set -a
source .env
set +a

if [[ -z "${KALSHI_API_KEY_ID:-}" ]]; then
    echo "ERROR: KALSHI_API_KEY_ID is not configured."
    exit 1
fi

if [[ -z "${KALSHI_PRIVATE_KEY_PATH:-}" ]]; then
    echo "ERROR: KALSHI_PRIVATE_KEY_PATH is not configured."
    exit 1
fi

if [[ ! -f "$KALSHI_PRIVATE_KEY_PATH" ]]; then
    echo "ERROR: Kalshi private key file not found:"
    echo "$KALSHI_PRIVATE_KEY_PATH"
    exit 1
fi

mkdir -p reports


supervise_service() {
    local name="$1"
    local log_path="$2"

    shift 2

    local child_pid=""
    local exit_code=0

    stop_child() {
        if [[ -n "$child_pid" ]]; then
            kill "$child_pid" 2>/dev/null || true
            wait "$child_pid" 2>/dev/null || true
        fi

        exit 143
    }

    trap stop_child TERM INT

    while true; do
        {
            echo
            echo "============================================================"
            echo "$(date -Is) Starting $name"
            echo "============================================================"
        } >> "$log_path"

        set +e

        PYTHONPATH=src "$PYTHON" -u "$@" \
            >> "$log_path" 2>&1 &

        child_pid=$!

        wait "$child_pid"
        exit_code=$?

        child_pid=""

        set -e

        if [[ "$exit_code" -eq 130 ]] \
            || [[ "$exit_code" -eq 143 ]]
        then
            echo \
                "$(date -Is) $name stopped with code $exit_code" \
                >> "$log_path"

            return "$exit_code"
        fi

        {
            echo
            echo "$(date -Is) $name exited unexpectedly with code $exit_code."
            echo "Restarting in 5 seconds..."
        } >> "$log_path"

        sleep 5
    done
}


stop_supervisor() {
    local pid="$1"

    if [[ -z "$pid" ]]; then
        return
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null || true
        return
    fi

    # Capture a currently running child before terminating
    # the supervisor. The supervisor's own TERM trap also
    # terminates its child, so this is an extra safeguard.
    local children=""

    if command -v pgrep >/dev/null 2>&1; then
        children="$(pgrep -P "$pid" 2>/dev/null || true)"
    fi

    kill "$pid" 2>/dev/null || true

    if [[ -n "$children" ]]; then
        kill $children 2>/dev/null || true
    fi

    wait "$pid" 2>/dev/null || true
}


cleanup() {
    local exit_code=$?

    trap - EXIT INT TERM

    echo
    echo "Stopping managed services..."

    stop_supervisor "$SYNC_SUPERVISOR_PID"
    stop_supervisor "$BTC_SUPERVISOR_PID"

    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi

    echo "Stopped."

    exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM


# ============================================================
# Dashboard HTTP server
# ============================================================

if command -v curl >/dev/null 2>&1 \
    && curl -fsS --max-time 0.3 \
        "http://127.0.0.1:8000/" \
        >/dev/null 2>&1
then
    echo "Dashboard server already running."
else
    echo "Starting dashboard server..."

    PYTHONPATH=src "$PYTHON" \
        -m kalshi_stats.cli serve \
        --host 127.0.0.1 \
        --port 8000 \
        > reports/server.log 2>&1 &

    SERVER_PID=$!

    sleep 0.5

    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "ERROR: dashboard server failed."
        cat reports/server.log
        exit 1
    fi
fi


# ============================================================
# Coinbase BTC live collector
# ============================================================

if pgrep -f \
    'python.*-m kalshi_stats\.btc_live' \
    >/dev/null 2>&1
then
    echo "Coinbase BTC collector already running."
else
    : > reports/btc_live.log

    supervise_service \
        "Coinbase BTC collector" \
        "reports/btc_live.log" \
        -m kalshi_stats.btc_live \
        --db "$DB" \
        > /dev/null 2>&1 &

    BTC_SUPERVISOR_PID=$!

    echo \
        "Coinbase BTC collector started " \
        "(log: reports/btc_live.log)"
fi


# Give Coinbase a moment to begin warming up before the
# synchronizer starts looking for fresh BTC feature rows.
sleep 1


# ============================================================
# Kalshi x BTC feature synchronizer
# ============================================================

if pgrep -f \
    'python.*-m kalshi_stats\.market_sync' \
    >/dev/null 2>&1
then
    echo "Market feature synchronizer already running."
else
    : > reports/market_sync.log

    supervise_service \
        "Kalshi x BTC synchronizer" \
        "reports/market_sync.log" \
        -m kalshi_stats.market_sync \
        --db "$DB" \
        --series KXBTC15M \
        > /dev/null 2>&1 &

    SYNC_SUPERVISOR_PID=$!

    echo \
        "Kalshi x BTC synchronizer started " \
        "(log: reports/market_sync.log)"
fi


echo
echo "Dashboard:"
echo "$URL"
echo

# Open dashboard in Windows default browser from WSL.
(
    sleep 1

    if command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe \
            -NoProfile \
            -Command \
            "Start-Process '$URL'" \
            >/dev/null 2>&1 \
            || true
    fi
) &


# ============================================================
# Kalshi dashboard monitor — foreground/main service
# ============================================================

echo "Starting Kalshi live dashboard..."
echo
echo "Managed services:"
echo "  Kalshi dashboard     foreground"
echo "  Coinbase BTC         reports/btc_live.log"
echo "  Kalshi x BTC sync    reports/market_sync.log"
echo
echo "Press Ctrl+C to stop everything started here."
echo

while true; do
    set +e

    PYTHONPATH=src "$PYTHON" \
        -m kalshi_stats.cli monitor \
        --db "$DB" \
        --scenarios "$SCENARIOS" \
        --output "$DASHBOARD" \
        --series KXBTC15M \
        "$@"

    EXIT_CODE=$?

    set -e

    if [[ "$EXIT_CODE" -eq 130 ]] \
        || [[ "$EXIT_CODE" -eq 143 ]]
    then
        exit "$EXIT_CODE"
    fi

    echo
    echo \
        "Kalshi monitor exited unexpectedly " \
        "with code $EXIT_CODE."
    echo "Restarting in 5 seconds..."
    echo

    sleep 5
done
