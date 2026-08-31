#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
docker build \
    --file "$repo_root/automation/container/Dockerfile" \
    --tag kalshi-stats-automation:phase-b-v1 \
    "$repo_root"
