# Automation V1 Control Plane

This directory is the durable control-plane home for future unattended research
and development. Conversation history is never authoritative. A fresh process
must reconstruct its work from repository documents, task JSON, run files, and
Git state.

Phase A defines state only. It does not run Codex, execute retries, create
worktrees, restart services, merge branches, deploy research, or submit Kalshi
orders.

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
present in `state.json`. Later phases must decide creation timing and durability
requirements for JSONL append operations.

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
