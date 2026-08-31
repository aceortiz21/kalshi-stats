# Automation V1 Checklist

This checklist is the durable roadmap for unattended research and development.
Checked items have a completed Phase A control-plane foundation in the repository;
unchecked items are not implemented and must not be inferred from documentation.

1. [ ] Git/runtime isolation
2. [x] hard security boundaries
3. [x] autonomous development container
4. [x] noninteractive Codex adapter
5. [x] machine-readable task queue
6. [ ] durable run state/context rollover
7. [ ] rate-limit handling
8. [ ] builder/reviewer pipeline
9. [ ] mechanical validation gates
10. [ ] runtime health supervisor
11. [ ] controlled research deployment
12. [ ] research history + compact handoff
13. [ ] research planner
14. [ ] daily/unattended report
15. [ ] unattended soak test
16. [ ] resume Phase 3B through automation

## Phase A completion boundary

Phase A supplies the policy contract, validated task/run records, atomic JSON
persistence, JSON Schemas, durable directory layout, and context-recovery
contract. The security-boundary item is checked because the prohibited targets
are explicit and `main` is rejected by the state model. Container isolation,
credential scanning, database-integrity enforcement, and process stop behavior
remain future enforcement work.

The task-queue item is checked only for its durable state model and persistence
foundation. It does not imply that a dispatcher exists. The context-rollover
item is intentionally unchecked: the required inputs, concrete per-run files,
fresh-context bootstrap, and session-ID capture exist, but no rollover/resume
supervisor exists yet.

Phase B provides a reproducible Python 3.12/Node 22/Codex 0.151.0 image and a
bounded noninteractive adapter with a no-process dry run. The adapter can execute
one already-selected task in an already-created, Git-verified worktree; it does
not select tasks, create worktrees, retry/resume, run a reviewer pipeline,
supervise the paper runtime, deploy research, merge branches, resume ML Phase 3B,
or enable real-money trading.

Git/runtime isolation remains unchecked because launch-time worktree enforcement
and isolated container Git metadata are implemented, but automated worktree
creation/lifecycle cleanup is not. Mechanical validation gates remain unchecked:
the runner records execution metadata and provides `validation.json`, but a
separate validation pipeline has not been implemented.
