#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 /absolute/path/to/dedicated-codex-home" >&2
    exit 2
fi

auth_root="$1"
if [[ "$auth_root" != /* || "$auth_root" == / || "$auth_root" == /home || "$auth_root" == "$HOME" ]]; then
    echo "Authentication path must be an absolute dedicated directory, not / or HOME" >&2
    exit 2
fi
if [[ -L "$auth_root" ]]; then
    echo "Authentication path must not be a symbolic link" >&2
    exit 2
fi
case "$auth_root" in
    "$HOME/.ssh"|"$HOME/.ssh/"*|"$HOME/.aws"|"$HOME/.aws/"*|\
    "$HOME/.config"|"$HOME/.config/"*|"$HOME/.codex"|"$HOME/.codex/"*|\
    "$HOME/stats"|"$HOME/stats/"*)
        echo "Authentication path overlaps a forbidden host credential/runtime directory" >&2
        exit 2
        ;;
esac
mkdir -p "$auth_root"
chmod 700 "$auth_root"

docker run \
    --rm \
    --interactive \
    --tty \
    --network bridge \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 256 \
    --memory 2g \
    --cpus 2 \
    --user "$(id -u):$(id -g)" \
    --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    --mount "type=bind,src=$auth_root,dst=/codex-home" \
    --env CODEX_HOME=/codex-home \
    --env HOME=/tmp/automation-home \
    kalshi-stats-automation:phase-b-v1 \
    codex login --device-auth
