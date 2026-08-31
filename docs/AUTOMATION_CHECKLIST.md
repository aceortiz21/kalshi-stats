# Automation V1 Checklist

This checklist is the durable roadmap for unattended research and development.
Checked items have a completed Phase A control-plane foundation in the repository;
unchecked items are not implemented and must not be inferred from documentation.

1. [ ] Git/runtime isolation
2. [x] hard security boundaries
3. [ ] autonomous development container
4. [ ] noninteractive Codex adapter
5. [x] machine-readable task queue
6. [x] durable run state/context rollover
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
item is checked because both the required inputs and per-run files are defined;
no Codex adapter or rollover executor exists yet.

Phase A does not execute tasks, retry failures, invoke Codex, supervise the paper
runtime, modify Git branches, deploy research, resume ML Phase 3B, or enable
real-money trading.
