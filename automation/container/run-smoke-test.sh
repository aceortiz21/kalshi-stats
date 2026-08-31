#!/usr/bin/env bash
set -euo pipefail

smoke_root="$(mktemp -d /tmp/kalshi-phase-b-smoke.XXXXXX)"
cleanup() {
    case "$smoke_root" in
        /tmp/kalshi-phase-b-smoke.*) rm -rf "$smoke_root" ;;
        *) echo "Refusing unsafe smoke cleanup path" >&2; exit 1 ;;
    esac
}
trap cleanup EXIT

docker run \
    --rm \
    --init \
    --network bridge \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 256 \
    --memory 2g \
    --cpus 2 \
    --user "$(id -u):$(id -g)" \
    --tmpfs /tmp:rw,nosuid,nodev,size=1g \
    --mount "type=bind,src=$smoke_root,dst=/workspace" \
    kalshi-stats-automation:phase-b-v1 \
    automation-smoke-test

test -f "$smoke_root/smoke-repository/result.txt"
test -f "$smoke_root/smoke.sqlite"
