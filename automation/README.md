# Automation V1 Control Plane

This directory is the durable control-plane home for future unattended research
and development. Conversation history is never authoritative. A fresh process
must reconstruct its work from repository documents, task JSON, run files, and
Git state.

Phase A defines the durable state model. Phase B adds an isolated development
container and a bounded noninteractive Codex runner. Phase C1 adds automated task
branch/worktree ownership and one frozen authenticated zero-approval canary path.
These phases do not choose research tasks, execute retries, restart services,
merge branches, deploy research, or submit Kalshi orders.

## Directory contract

```text
automation/
├── README.md
├── history/              # compact durable history; generated entries ignored
├── policies/             # binding Git, safety, and research contracts
├── runs/                 # one generated directory per run; ignored by default
├── schemas/              # JSON Schemas for persisted records
├── state/                # future scheduler/global state; ignored by default
└── tasks/                # generated task JSON records; ignored by default
```

Placeholder files preserve the generated-data directories. Runtime content is
ignored by `automation/.gitignore`; intentionally curated examples or history
must be force-added only after human review.

The Python state implementation is
`src/kalshi_stats/automation_state.py`. Persisted task records use
`automation/schemas/task.schema.json`; run records use
`automation/schemas/run.schema.json`.

## Task files

One task is stored as one JSON object, normally:

```text
automation/tasks/<task_id>.json
```

Writers must use atomic replacement. Task state must obey the transition table
implemented in `ALLOWED_TASK_TRANSITIONS`. `main` is not a valid autonomous
branch. The only autonomous integration target is `automation-integration`;
isolated task branches use `auto/<task-name>` or
`automation/<task-name>`.

## Run directory contract

Every future run uses a stable directory such as:

```text
automation/runs/<run_id>/
├── HANDOFF.md
├── state.json
├── events.jsonl
├── final.md
├── validation.json
└── errors.log
```

- `HANDOFF.md` is the concise current-state and continuation brief.
- `state.json` is the authoritative `RunRecord`.
- `events.jsonl` is an append-only structured event stream.
- `final.md` stores the run's final response.
- `validation.json` stores commands, exit codes, and validation results.
- `errors.log` stores diagnostic output; it must never contain credentials.

Files may begin empty when a run is created, but their expected paths must be
present in `state.json`. Phase B now creates this exact six-file contract. JSON
state and validation writes use atomic replacement; runner metadata events are
appended and fsynced. During actual execution, Codex JSONL stdout streams into
`events.jsonl`, stderr streams into `errors.log`, and the Codex
`--output-last-message` path is `final.md`.

## Context recovery contract

Before changing code, a fresh future Codex run must read, in this order:

1. `AGENTS.md` in full.
2. `README.md` in full.
3. `docs/RESEARCH_SYSTEM.md` in full.
4. `docs/CODEX_HANDOFF.md` in full.
5. `docs/AUTOMATION_CHECKLIST.md` in full.
6. The current task JSON in full.
7. The current run HANDOFF.md in full.
8. Current `git status`, `git diff`, and relevant recent `git log`.
9. The prior validation results from the current run's `validation.json`, plus
   any paths referenced by the task record.

The run may continue only after reconciling those sources. If sources disagree,
the implementation and tests control code behavior, the policy files control
automation authority, and ambiguity must be recorded rather than silently
resolved. The fresh run must update durable files as it progresses so another
run can continue without access to any previous conversation.

At minimum, `HANDOFF.md` must identify the task and run, current objective,
completed work, remaining work, relevant decisions/assumptions, files changed,
last validation status, active blocker or error classification, and exact next
action.

## Error classification and future behavior

Persisted error classes are defined by `ErrorClassification` and
`error-classification.schema.json`:

- `SUCCESS`: terminal successful run; no retry.
- `RATE_LIMITED`: preserve state and wait for quota; later retry only under the
  future quota policy.
- `CONTEXT_EXHAUSTED`: write a complete handoff and start a fresh context later.
- `CODE_FAILURE`: retain evidence and allow a bounded future attempt.
- `TEST_FAILURE`: retain validation output; do not pass or integrate.
- `INFRASTRUCTURE_FAILURE`: preserve state and retry only after infrastructure
  health is restored.
- `SECURITY_VIOLATION`: stop automation immediately; never retry automatically.
- `DATABASE_INTEGRITY_FAILURE`: stop automation immediately and preserve the
  database/evidence for human investigation.
- `UNKNOWN_FAILURE`: stop conservatively pending classification or human review.

Phase A performs no retries. `attempt_count` is incremented only when a queued
task enters `RUNNING`; quota resumption does not consume another attempt.

## Phase B isolated runner

`src/kalshi_stats/automation_runner.py` consumes already-created `TaskRecord` and
`RunRecord` files. It verifies the task path and branch against Git's registered
worktree metadata, the permitted automation-worktree root, and the separately
configured primary runtime worktree. It rejects `main`,
`automation-integration`, path escapes, record/Git disagreement, unrelated Git
repositories, broad auth mounts, and unknown or credential-like environment
variables as `SECURITY_VIOLATION`.

The runner supports task/run IDs through their records, task worktree, prompt
path, run directory, dedicated auth directory, image, optional model/reasoning
effort, timeout, explicit environment-name allowlist, and required dry-run mode.
Dry-run performs all boundary checks and renders a redacted launch plan without
creating a Git bundle or starting Docker/Codex.

Fresh-context bootstrap text directs Codex to read the five repository sources,
task JSON, current handoff, Git status/diff/log, and prior validation before any
change. This makes a fresh run recoverable from durable inputs, but Phase B does
not yet implement automatic context rollover or `codex exec resume` scheduling.

See `automation/container/README.md` for the exact image, mounts, Codex 0.151.0
syntax, threat boundary, authentication bootstrap, and smoke test.

## Phase C1 worktree lifecycle and canary

`src/kalshi_stats/automation_worktrees.py` owns bounded creation, inspection, and
cleanup of task worktrees. A caller must explicitly allow the source branch and
configure a narrow automation-worktree root. Task branches still must match the
Phase A policy and can never be `main` or `automation-integration`. Creation and
inspection reconcile the path, checked-out branch, registered Git worktree pair,
and common Git directory. Paths outside the configured root, indirect child paths,
symlink paths, unrelated repositories, existing branches, and conflicting
worktrees fail closed.

Ordinary cleanup refuses any tracked or untracked change. The deliberate canary
cleanup mode additionally accepts only caller-enumerated ignored paths, refuses
unknown ignored content, refuses task branches containing commits, and uses
non-forced `git worktree remove` plus `git branch -d`. Canary evidence is copied
to the initiating worktree's ignored `automation/runs/<run-id>/` directory before
the disposable task worktree and branch are removed.

`src/kalshi_stats/automation_canary.py` implements the concrete bounded flow:

```text
frozen canary definition
  -> TaskRecord
  -> task branch
  -> isolated worktree
  -> RunRecord
  -> six-file run directory + HANDOFF
  -> Phase B runner
  -> exact-output/zero-approval validation
  -> evidence preservation
  -> guarded disposable cleanup
```

The source-controlled prompt is `automation/canary/PROMPT.md`. It may write only
the named `canary-output.txt` inside its ignored run directory and explicitly
forbids credentials, `main`, the primary `~/stats` runtime, unrelated changes,
and network access other than the Codex client's own service call. The validator
requires exact output, populated JSONL/final evidence, recorded zero exit,
`SUCCESS`, `--ask-for-approval never`, and zero approval-request events.

This is a single infrastructure canary path, not a dispatcher, general mechanical
validation pipeline, retry/resume supervisor, reviewer pipeline, runtime
supervisor, integration merger, research planner, or ML Phase 3B resumption.

## Phase C2B dispatcher

`src/kalshi_stats/automation_dispatcher.py` is a host-side single-worker
dispatcher. It locks the queue with an OS file lock, recovers ambiguous
`RUNNING` records as `BLOCKED`, selects `QUEUED` tasks by priority/creation
time/task ID after all prerequisites are `PASSED`, creates the task branch and
worktree, and creates the six-file run contract. It delegates every execution
to `run_with_quota_wait`; it does not invoke Codex directly. A failed
dependency is never runnable, and `WAITING_FOR_QUOTA` is resumed as the same
attempt rather than duplicated.

The minimal user interface is:

```text
PYTHONPATH=src python -m kalshi_stats.automation_cli submit --title TITLE --spec SPEC.md
PYTHONPATH=src python -m kalshi_stats.automation_cli list
PYTHONPATH=src python -m kalshi_stats.automation_cli status TASK_ID
PYTHONPATH=src python -m kalshi_stats.automation_cli run [--once|--continuous]
```

`CONTEXT_EXHAUSTED` preserves the prior run and handoff, creates a fresh
continuation run with a bounded rollover count, and reconstructs context from
disk. Exceeding the configured limit fails closed. A restarted dispatcher can
resume durable quota waiting; an old `RUNNING` record is treated as ambiguous
and blocked pending human review.
