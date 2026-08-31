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
BRTI_SUPERVISOR_PID=""
SYNC_SUPERVISOR_PID=""
ACCOUNT_SUPERVISOR_PID=""
PROSPECTIVE_SUPERVISOR_PID=""
TRIGGER_SHADOW_SUPERVISOR_PID=""
SHADOW_LAB_SUPERVISOR_PID=""
CHALLENGER_SUPERVISOR_PID=""
PAPER_BROKER_SUPERVISOR_PID=""

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

    stop_supervisor "$PAPER_BROKER_SUPERVISOR_PID"
    stop_supervisor "$CHALLENGER_SUPERVISOR_PID"
    stop_supervisor "$SHADOW_LAB_SUPERVISOR_PID"
    stop_supervisor "$TRIGGER_SHADOW_SUPERVISOR_PID"
    stop_supervisor "$PROSPECTIVE_SUPERVISOR_PID"
    stop_supervisor "$ACCOUNT_SUPERVISOR_PID"
    stop_supervisor "$SYNC_SUPERVISOR_PID"
    stop_supervisor "$BRTI_SUPERVISOR_PID"
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
# Official Kalshi / CF Benchmarks BRTI collector
# ============================================================

if pgrep -f     'python.*-m kalshi_stats\.brti_live'     >/dev/null 2>&1
then
    echo "BRTI collector already running."
else
    : > reports/brti_live.log

    supervise_service         "Kalshi BRTI collector"         "reports/brti_live.log"         -m kalshi_stats.brti_live         --db "$DB"         --index BRTI         > /dev/null 2>&1 &

    BRTI_SUPERVISOR_PID=$!

    echo         "Official BRTI collector started "         "(log: reports/brti_live.log)"
fi


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



# ============================================================
# Personal Kalshi account synchronizer
# ============================================================

if pgrep -f \
    'python.*-m kalshi_stats\.account_sync' \
    >/dev/null 2>&1
then
    echo "Account synchronizer already running."
else
    : > reports/account_sync.log

    supervise_service \
        "Kalshi account synchronizer" \
        "reports/account_sync.log" \
        -m kalshi_stats.account_sync \
        --db "$DB" \
        --interval 15 \
        > /dev/null 2>&1 &

    ACCOUNT_SUPERVISOR_PID=$!

    echo \
        "Kalshi account synchronizer started " \
        "(log: reports/account_sync.log)"
fi


# ============================================================
# Prospective opportunity / fill-state logger
# ============================================================

if pgrep -f \
    'python.*-m kalshi_stats\.prospective_logger' \
    >/dev/null 2>&1
then
    echo "Prospective logger already running."
else
    : > reports/prospective.log

    supervise_service \
        "Prospective evidence logger" \
        "reports/prospective.log" \
        -m kalshi_stats.prospective_logger \
        --db "$DB" \
        > /dev/null 2>&1 &

    PROSPECTIVE_SUPERVISOR_PID=$!

    echo \
        "Prospective evidence logger started " \
        "(log: reports/prospective.log)"
fi



# ============================================================
# Main-trigger confirmation / shadow execution researcher
# ============================================================

if pgrep -f \
    'python.*-m kalshi_stats\.trigger_shadow' \
    >/dev/null 2>&1
then
    echo "Trigger shadow researcher already running."
else
    : > reports/trigger_shadow.log

    supervise_service \
        "Trigger confirmation / shadow researcher" \
        "reports/trigger_shadow.log" \
        -m kalshi_stats.trigger_shadow \
        --db "$DB" \
        --interval 1 \
        > /dev/null 2>&1 &

    TRIGGER_SHADOW_SUPERVISOR_PID=$!

    echo \
        "Trigger shadow researcher started " \
        "(log: reports/trigger_shadow.log)"
fi



# ============================================================
# Predeclared forward Strategy Zoo
# ============================================================

echo "Registering forward Strategy Zoo..."

PYTHONPATH=src "$PYTHON" \
    -m kalshi_stats.strategy_zoo \
    --db "$DB" \
    --once



# ============================================================
# Realistic Kalshi PaperBroker
# ============================================================

if pgrep -f \
    'python.*-m kalshi_stats\.paper_broker' \
    >/dev/null 2>&1
then
    echo "PaperBroker already running."
else
    : > reports/paper_broker.log

    supervise_service \
        "Realistic Kalshi PaperBroker" \
        "reports/paper_broker.log" \
        -m kalshi_stats.paper_broker \
        --db "$DB" \
        --interval 0.25 \
        --starting-cash 10 \
        --trade-notional 1.00 \
        > /dev/null 2>&1 &

    PAPER_BROKER_SUPERVISOR_PID=$!

    echo \
        "PaperBroker started " \
        "(log: reports/paper_broker.log)"
fi



# ============================================================
# Continuous multi-strategy Shadow Lab
# ============================================================

if pgrep -f \
    'python.*-m kalshi_stats\.shadow_lab' \
    >/dev/null 2>&1
then
    echo "Continuous Shadow Lab already running."
else
    : > reports/shadow_lab.log

    supervise_service \
        "Continuous Shadow Lab" \
        "reports/shadow_lab.log" \
        -m kalshi_stats.shadow_lab \
        --db "$DB" \
        --interval 30 \
        > /dev/null 2>&1 &

    SHADOW_LAB_SUPERVISOR_PID=$!

    echo \
        "Continuous Shadow Lab started " \
        "(log: reports/shadow_lab.log)"
fi



# ============================================================
# Automatic challenger generator
# ============================================================

if pgrep -f \
    'python.*-m kalshi_stats\.challenger_generator' \
    >/dev/null 2>&1
then
    echo "Challenger generator already running."
else
    : > reports/challenger_generator.log

    supervise_service \
        "Automatic Challenger Generator" \
        "reports/challenger_generator.log" \
        -m kalshi_stats.challenger_generator \
        --db "$DB" \
        --interval 900 \
        > /dev/null 2>&1 &

    CHALLENGER_SUPERVISOR_PID=$!

    echo \
        "Challenger generator started " \
        "(log: reports/challenger_generator.log)"
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
echo "  Official BRTI        reports/brti_live.log"
echo "  Kalshi x BTC sync    reports/market_sync.log"
echo "  Account tracking     reports/account_sync.log"
echo "  Prospective logger   reports/prospective.log"
echo "  Trigger shadow       reports/trigger_shadow.log"
echo "  PaperBroker          reports/paper_broker.log"
echo "  Continuous lab       reports/shadow_lab.log"
echo "  Challenger generator reports/challenger_generator.log"
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
