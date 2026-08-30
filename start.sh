#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.venv/bin/python"
URL="http://127.0.0.1:8000/reports/dashboard.html"
SERVER_PID=""

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


cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM


# Start the local web server only if one is not already running.
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


echo
echo "Dashboard:"
echo "$URL"
echo

# Open the dashboard in the Windows default browser from WSL.
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


echo "Starting Kalshi live dashboard..."
echo "Press Ctrl+C to stop."
echo

PYTHONPATH=src "$PYTHON" \
    -m kalshi_stats.cli monitor \
    --db data/kalshi_stats_snapshot.sqlite \
    --scenarios config/scenarios.json \
    --output reports/dashboard.html \
    --series KXBTC15M \
    "$@"
