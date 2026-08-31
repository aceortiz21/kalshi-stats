#!/usr/bin/env bash
set -euo pipefail

test -d /workspace
curl --fail --silent --show-error https://pypi.org/simple/idna/ >/dev/null

python -m venv /tmp/phase-b-smoke-venv
/tmp/phase-b-smoke-venv/bin/python -m pip install --quiet --no-cache-dir idna==3.10
/tmp/phase-b-smoke-venv/bin/python -c "import idna; assert idna.__version__ == '3.10'"

git --version
git init --quiet /workspace/smoke-repository
git -C /workspace/smoke-repository config user.name "Phase B Smoke Test"
git -C /workspace/smoke-repository config user.email "phase-b-smoke.invalid"
printf 'container-write-ok\n' > /workspace/smoke-repository/result.txt
git -C /workspace/smoke-repository add result.txt
git -C /workspace/smoke-repository commit --quiet -m "Smoke test"

sqlite3 /workspace/smoke.sqlite \
    "CREATE TABLE smoke(value TEXT); INSERT INTO smoke VALUES('sqlite-ok');"
test "$(sqlite3 /workspace/smoke.sqlite 'SELECT value FROM smoke;')" = "sqlite-ok"
test "$(cat /workspace/smoke-repository/result.txt)" = "container-write-ok"

printf 'phase-b-container-smoke: PASS\n'
