# Git Policy

Policy ID: `AUTOMATION_GIT_V1`

Applies to: every automated task and run

Default on uncertainty: stop and record `BLOCKED`

| Rule ID | Requirement |
| --- | --- |
| GIT-001 | `main` is human-only. Automation MUST NOT modify, merge into, or push `main`. |
| GIT-002 | `automation-integration` is the highest and only autonomous integration destination. It is not a task work branch. |
| GIT-003 | Task and ordinary run work branches MUST use `auto/<task-name>` or `automation/<task-name>`. Their records MUST NOT use `automation-integration`. |
| GIT-004 | Each task MUST operate in its own isolated Git worktree. |
| GIT-005 | Automation MUST NOT directly merge or push to `main`. Promotion from `automation-integration` requires explicit human action. |
| GIT-006 | A task MUST record its exact branch and worktree before execution. Branch/worktree disagreement is blocking. |
| GIT-007 | Automation MUST preserve unrelated and pre-existing user changes. |

Phase A separately validates task work branches and the future autonomous
integration destination. It does not yet create worktrees, run Git commands,
push, or merge.
