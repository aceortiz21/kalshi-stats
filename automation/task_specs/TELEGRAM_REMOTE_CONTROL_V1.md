# Telegram Remote Control + Notifications V1

Implement ONLY the minimum host-side Telegram control layer needed so the
operator can leave the computer while Automation V1 works.

Do not implement:
- research planner
- autonomous strategy selection
- Kalshi live trading
- Kalshi write credentials
- runtime deployment
- arbitrary remote shell access
- automatic main merges
- unrestricted process control

The existing C3 builder -> validation -> independent reviewer pipeline must
remain authoritative.

============================================================
PRIMARY GOAL
============================================================

Create a persistent host-side Telegram controller for Automation V1.

The controller must allow the operator to monitor and safely control the
automation worker from the configured Telegram chat.

Supported V1 commands:

/help
/status
/health
/queue
/task <task-id>
/worker
/worker-start
/worker-stop
/recover
/idea <text>

Also send automatic Telegram notifications for:

PASSED
FAILED
BLOCKED
WAITING_FOR_QUOTA

Do not spam unchanged status repeatedly.

============================================================
SECURITY
============================================================

Telegram MUST NOT become a remote shell.

Forbidden:
- /shell
- /exec
- arbitrary subprocess commands supplied by Telegram
- arbitrary file reads
- arbitrary file writes
- arbitrary process IDs supplied by Telegram
- changing Git main
- weakening security policy
- accessing Kalshi trading credentials

Only accept commands from the configured exact TELEGRAM_CHAT_ID.

Ignore/reject every other chat.

Never log or print TELEGRAM_BOT_TOKEN.

Never persist the bot token in Git, task evidence, diagnostics, or tests.

Do not access the user's real Telegram secret during autonomous development.

The real host secret already exists outside Git at:

~/.local/share/kalshi-stats-automation/telegram.env

It contains:
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID

Implementation must support loading that file at HOST runtime, but unit tests
must use temporary fake credentials only.

The Telegram secret must not be mounted into autonomous Codex containers.

============================================================
HOST CONTROLLER
============================================================

Implement a small host-side controller with durable PID/state/log files under:

~/.local/share/kalshi-stats-automation/runtime/

Provide a simple host bootstrap command/script so the operator does not need
to remember long PYTHONPATH, Docker, worktree, or nohup commands.

Desired eventual usage should be approximately:

./automationctl start
./automationctl stop
./automationctl status
./automationctl recover

Exact implementation may differ slightly if justified.

The controller should manage ONLY known Automation V1 processes using
controller-owned PID/state files.

Never kill arbitrary processes.

============================================================
TELEGRAM BEHAVIOR
============================================================

/status
Return concise overall automation state.

/health
Report at minimum:
- Telegram controller
- automation worker
- Docker availability
- queue accessibility
- whether human intervention is required

/queue
Return concise queued/running/validating/reviewing/waiting/blocked tasks.

/task <id>
Return concise task status and next action.

/worker
Return worker status.

/worker-start
Safely start the continuous dispatcher if it is not already running.

/worker-stop
Safely stop only the controller-owned dispatcher process.

/recover
Perform a bounded safe recovery:
1. inspect controller state
2. inspect Docker availability
3. inspect queue/task state
4. detect stale controller PID/state
5. restart the automation worker when it is safe
6. fail closed if task ownership is ambiguous
7. return a concise recovery report

/recover MUST NOT override BLOCKED safety states.

/idea <text>
Persist the idea into a durable idea inbox for later planning.
Do NOT automatically execute vague ideas in V1.

============================================================
WORKER
============================================================

Use the C3-capable automation dispatcher in continuous mode.

The host controller should launch it with existing safety parameters:
- allowed task worktree root
- primary runtime worktree protection
- dedicated automation Codex auth
- isolated Docker image
- no autonomous main
- existing C2A quota handling
- existing C2B durable state
- existing C3 validation/reviewer pipeline

Do not duplicate the dispatcher.

============================================================
NOTIFICATIONS
============================================================

Implement durable transition-aware notifications.

Notify once when a task enters:
- PASSED
- FAILED
- BLOCKED
- prolonged WAITING_FOR_QUOTA

Message should include:
- task id/title
- status
- concise next action/reason

Never include secrets or huge logs.

============================================================
RESTART / OFFLINE BEHAVIOR
============================================================

Internet loss must not corrupt durable state.

If Telegram API is temporarily unavailable:
- controller stays alive where practical;
- retry with bounded backoff;
- do not lose automation task state.

If the automation worker dies:
- /health reports it;
- /recover may restart it when safe.

If the entire computer is powered off, no claim should be made that Telegram
can reach the local controller.

Provide a clean foundation for later Windows/WSL automatic startup, but do
not implement broad Windows host changes in this task.

============================================================
TESTS
============================================================

Add focused mocked tests for at least:

- only configured chat ID is authorized
- unauthorized chats cannot execute commands
- token is never emitted to logs/messages/state
- /status
- /health
- /queue
- /task
- /worker-start does not start duplicate worker
- /worker-stop only stops controller-owned process
- /recover safely restarts a stopped worker
- /recover refuses ambiguous running-task ownership
- /recover does not bypass BLOCKED
- /idea persists but does not execute
- terminal notification deduplication
- temporary Telegram/network failure retry behavior
- no arbitrary shell command path exists
- existing C3 validation/review still gates PASS

Use mocked Telegram HTTP calls.
Do NOT use the real Telegram bot during autonomous tests.

============================================================
DOCUMENTATION
============================================================

Create/update a concise operator guide showing:

1. one command to start the controller
2. one command to stop it
3. one command to check it
4. Telegram commands
5. what /recover does
6. what still requires physical computer access
7. where logs/state live
8. how to recover after a normal process crash

Keep the guide concise.

============================================================
VALIDATION
============================================================

Run:
- compileall
- full pytest
- shell syntax checks
- JSON/TOML/config parsing where applicable
- git diff --check
- C3 mechanical validation
- independent reviewer

Verify:
- no Telegram secret persisted
- no Kalshi credential access
- main unchanged
- ~/stats untouched
- real-money execution unchanged
- no runtime restart
- no arbitrary shell interface

DO NOT COMMIT.
Stop for independent review.
