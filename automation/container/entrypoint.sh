#!/usr/bin/env bash
set -euo pipefail

umask 077
mkdir -p "${HOME:?}" "${CODEX_HOME:-/codex-home}"

if [[ -n "${AUTOMATION_TASK_BRANCH:-}" || -n "${AUTOMATION_RUN_RELATIVE:-}" ]]; then
    if [[ ! "${AUTOMATION_TASK_BRANCH:-}" =~ ^(auto|automation)/[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$ ]]; then
        echo "SECURITY_VIOLATION: invalid task branch passed to container" >&2
        exit 70
    fi
    if [[ ! "${AUTOMATION_RUN_RELATIVE:-}" =~ ^automation/runs/[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
        echo "SECURITY_VIOLATION: invalid run path passed to container" >&2
        exit 70
    fi

    bundle="/workspace/${AUTOMATION_RUN_RELATIVE}/.repository.bundle"
    git_dir="/tmp/automation-task-git"
    if [[ ! -f "$bundle" ]]; then
        echo "INFRASTRUCTURE_FAILURE: isolated Git bundle is missing" >&2
        exit 71
    fi
    rm -rf "$git_dir"
    git init --bare --quiet "$git_dir"
    git --git-dir="$git_dir" fetch --quiet \
        "$bundle" \
        "refs/heads/${AUTOMATION_TASK_BRANCH}:refs/heads/${AUTOMATION_TASK_BRANCH}"
    git --git-dir="$git_dir" symbolic-ref HEAD "refs/heads/${AUTOMATION_TASK_BRANCH}"
    git --git-dir="$git_dir" --work-tree=/workspace read-tree HEAD
    export GIT_DIR="$git_dir"
    export GIT_WORK_TREE=/workspace
fi

exec "$@"
