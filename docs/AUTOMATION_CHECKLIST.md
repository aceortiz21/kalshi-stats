# Automation V1 Checklist

This checklist is the durable roadmap for unattended research and development.
Checked items have a completed Phase A control-plane foundation in the repository;
unchecked items are not implemented and must not be inferred from documentation.

1. [x] Git/runtime isolation
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

Phase C1 implements and tests automated task branch/worktree creation,
Git-backed ownership inspection, dirty/unknown-work cleanup refusal, and a narrow
disposable-canary cleanup path. The frozen canary uses the real TaskRecord ->
worktree -> RunRecord -> HANDOFF -> Phase B runner path. Item 1 is checked on that
basis. Main remains human-only, `automation-integration` is not a task branch,
the primary runtime is excluded, and no merge is automatic.

Phase C1 also adds authenticated canary-specific success validation, including
the exact output and zero-approval criterion. Mechanical validation gates remain
unchecked because there is still no general task validation pipeline. Item 6
remains incomplete: durable recovery inputs and session-ID capture exist, but
automatic context rollover/resume belongs to Phase C2.

The separately authorized second canary (`phase-c1-auth-canary-002`) completed
the full authenticated path after the Docker stdin fix: Codex started a session,
received the frozen prompt, produced the exact output and final response, exited
zero, exposed a thread ID, and requested zero approvals. Its task and run reached
`PASSED`; evidence was preserved before guarded disposable cleanup. This closes
the Phase C1 authenticated-canary proof. Items 6 through 16 remain unchanged.
