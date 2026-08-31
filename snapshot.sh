#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")"

PYTHONPATH=src .venv/bin/python \
  -m kalshi_stats.paper_snapshot \
  --db data/kalshi_stats_snapshot.sqlite \
  --out reports/paper_engine_snapshot.json

echo
ls -lh reports/paper_engine_snapshot.json
