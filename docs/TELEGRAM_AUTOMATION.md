# Telegram Automation V1

Run these commands from the Automation V1 repository checkout:

```bash
./automationctl start
./automationctl stop
./automationctl status
./automationctl recover
```

`start` loads `~/.local/share/kalshi-stats-automation/telegram.env` on the host
and starts the persistent controller. The file must define
`TELEGRAM_BOT_TOKEN` and the one authorized `TELEGRAM_CHAT_ID`. Never copy it
into this repository or an autonomous worktree.
The dispatcher uses the dedicated automation Codex authentication directory at
`~/.local/share/kalshi-stats-automation/codex-home` by default.

`/worker-start` and `/recover` fail closed for invalid queue records, `BLOCKED`
tasks, ambiguous active-task ownership, or unavailable Docker. `/worker-stop`
refuses to interrupt `RUNNING`, `VALIDATING`, or `REVIEWING` task ownership.
Invalid task records also make health require human intervention and prevent
worker process control until the queue is repaired.

The authorized chat supports `/help`, `/status`, `/health`, `/queue`, `/task
<task-id>`, `/worker`, `/worker-start`, `/worker-stop`, `/recover`, and `/idea
<text>`. Ideas are saved for later planning and are never executed.

`/recover` and `./automationctl recover` clear stale controller-owned worker
records and restart the existing continuous C3 dispatcher only when Docker and
task ownership are safe. They refuse `BLOCKED` tasks and ambiguous active-task
ownership. They do not alter task state, bypass validation/review, merge, deploy,
or control arbitrary processes.

State, PID records, and logs live under
`~/.local/share/kalshi-stats-automation/runtime/`; ideas are stored in
`~/.local/share/kalshi-stats-automation/ideas.jsonl`. After an ordinary
controller crash, run `./automationctl start`. After a worker crash, inspect
`./automationctl status` and use `./automationctl recover`.

Physical computer access is still required after power-off, for Docker/WSL or
network repair, credential changes, ambiguous/blocked safety states, human Git
review and merges, and any runtime deployment. Telegram cannot reach a powered
off local computer and provides no shell or live-trading authority.
