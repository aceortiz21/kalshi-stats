# Automation V1 Phase C3 — Independent Reviewer + Validation Gates

Implement ONLY Automation V1 Phase C3.

The purpose of this phase is to make future autonomous development trustworthy
enough that a builder cannot simply declare its own work successful.

Do NOT implement:
- runtime/dashboard supervision
- autonomous integration deployment
- main merges
- research planner
- scientific experiment selection
- ML Phase 3B
- real-money trading
- Kalshi write credentials

Read completely before editing:

1. AGENTS.md
2. README.md
3. docs/RESEARCH_SYSTEM.md
4. docs/CODEX_HANDOFF.md
5. docs/AUTOMATION_CHECKLIST.md
6. automation/README.md
7. automation/policies/GIT_POLICY.md
8. automation/policies/SAFETY_POLICY.md
9. automation/policies/RESEARCH_POLICY.md
10. src/kalshi_stats/automation_state.py
11. src/kalshi_stats/automation_runner.py
12. src/kalshi_stats/automation_quota.py
13. src/kalshi_stats/automation_worktrees.py
14. src/kalshi_stats/automation_dispatcher.py
15. relevant tests and schemas

Verify:
- current branch is a valid automation task branch
- worktree is under the permitted automation task root
- worktree is not ~/stats
- main remains human-only
- automation-integration is not used as a task branch
- live-money execution remains disabled

============================================================
PRIMARY OBJECTIVE
============================================================

Implement an autonomous validation/review pipeline:

BUILDER
  ->
MECHANICAL VALIDATION
  ->
INDEPENDENT REVIEWER
  ->
PASS

or:

REVIEWER REQUESTS CHANGES
  ->
fresh bounded builder repair
  ->
mechanical validation again
  ->
fresh independent reviewer
  ->
PASS / FAIL / BLOCK

The builder must never be allowed to approve its own work.

============================================================
INDEPENDENT REVIEWER
============================================================

Reviewer requirements:

- fresh Codex invocation/session;
- never reuse the builder's Codex session/thread;
- reconstruct context from durable repository/task/run state;
- inspect task specification;
- inspect Git status/diff/log;
- inspect validation results;
- inspect relevant changed code/tests;
- inspect repository policies;
- provide machine-readable verdict;
- do not make code edits;
- any reviewer modification to the task worktree must be detected and fail closed.

Reviewer verdicts:

PASS
CHANGES_REQUIRED
BLOCKED

Review output should include structured findings with severity where practical.

At minimum distinguish:
- CRITICAL
- HIGH
- MEDIUM
- LOW

A PASS is allowed only when no blocking finding remains and every required
mechanical validation gate passed.

Persist reviewer evidence separately from builder evidence.

Persist distinct builder/reviewer session identifiers when available.

============================================================
MECHANICAL VALIDATION GATES
============================================================

Create a reusable validation component rather than scattering shell commands.

For ordinary Python repository development tasks, support gates equivalent to:

- compileall
- full pytest
- git diff --check
- shell syntax checks for relevant shell files
- JSON parsing/schema validation where applicable
- TOML parsing where applicable
- task branch/worktree safety validation
- changed-file policy validation
- credential/secret diagnostic scan
- forbidden live-system path/access checks

Never weaken or delete existing tests simply to achieve PASS.

A failed required gate must prevent reviewer PASS.

Store machine-readable validation results.

============================================================
SECRET / CREDENTIAL SAFETY
============================================================

Add deterministic protection against accidentally persisting secrets.

Do not print credential contents.

Detect or fail closed on likely:
- private-key material
- bearer tokens
- OpenAI/API-key shaped secrets
- Kalshi credential variables/material
- auth material outside the dedicated automation Codex auth mount

Do not scan/read the contents of protected host credential directories.

The purpose is to prevent new automation outputs/diffs/logs from leaking
credentials, not to inventory user secrets.

============================================================
DATABASE / SCIENTIFIC EVIDENCE SAFETY
============================================================

Build a reusable integrity gate foundation for later research tasks.

For this phase:
- do not access the live research database;
- do not modify scientific evidence;
- do not run research experiments.

Future validation must be able to fail closed if a task unexpectedly modifies
a protected database/evidence artifact.

Do not claim full research deployment safety unless it is actually implemented.

============================================================
REPAIR LOOP
============================================================

Implement a SMALL bounded repair loop.

Default maximum reviewer repair cycles: 2.

For CHANGES_REQUIRED:

1. preserve reviewer findings;
2. create a fresh builder invocation;
3. provide the task specification plus exact reviewer findings;
4. builder repairs the same isolated task worktree;
5. rerun all mechanical gates;
6. invoke a fresh independent reviewer.

Reviewer sessions must remain independent from builder sessions.

Do not retry:
- SECURITY_VIOLATION
- DATABASE_INTEGRITY_FAILURE
- clearly blocked policy violations

Those fail closed.

RATE_LIMITED remains delegated to existing C2A.

Context exhaustion remains delegated to existing durable continuation machinery.

============================================================
AUTOMATION INTEGRATION
============================================================

Integrate review into the dispatcher architecture cleanly.

Future task lifecycle should effectively become:

QUEUED
->
RUNNING builder
->
VALIDATING
->
REVIEWING
->
PASSED

or bounded repair/failure/block.

Do not bypass the existing TaskRecord transition model.

Do not create a second unrelated task system.

Do not merge the task branch anywhere automatically.

============================================================
HISTORY / EVIDENCE
============================================================

Durably record:
- builder run ID/session
- mechanical validation result
- reviewer run ID/session
- reviewer verdict
- findings
- repair-cycle count
- final decision
- failure/block reason

Logs/history must not contain secret values.

============================================================
TESTS
============================================================

Add focused tests for at least:

- builder cannot directly mark its own task PASS without gates/review
- failed mechanical validation prevents review PASS
- reviewer is a distinct invocation/session
- reviewer receives task spec + diff + validation evidence
- reviewer PASS produces PASSED
- CHANGES_REQUIRED invokes bounded fresh builder repair
- repaired task is revalidated before another review
- maximum repair cycles fail closed
- SECURITY_VIOLATION is never auto-repaired
- DATABASE_INTEGRITY_FAILURE is never auto-repaired
- reviewer modifying worktree is detected
- structured reviewer result is persisted
- history records builder/reviewer separately
- secrets are redacted/not persisted
- RATE_LIMITED still uses C2A instead of duplicate logic
- context continuation still uses C2B machinery
- main is rejected
- automation-integration is rejected as task branch
- ~/stats is rejected
- no live database is accessed

Use mocked/simulated Codex results for failure-path tests.
Do not intentionally consume real quota for rate-limit tests.

============================================================
REAL C3 CANARY
============================================================

After all tests pass, run exactly ONE harmless real builder/reviewer canary.

Builder task:
- create one harmless exact canary output under ignored run evidence;
- make no unrelated changes.

Then:
- mechanical gates run;
- a fresh authenticated reviewer Codex session reviews it;
- autonomous approval prompts = 0;
- reviewer must make no worktree edits;
- final verdict must be PASS;
- evidence must show distinct builder and reviewer sessions.

If the real canary fails:
- preserve evidence;
- make only the smallest necessary fix;
- do NOT run a second real canary this turn.

============================================================
VALIDATION
============================================================

Use the isolated task environment.

Run:

PYTHONPATH=src .venv/bin/python -m compileall -q src tests
PYTHONPATH=src .venv/bin/python -m pytest -q
bash -n start.sh
bash -n snapshot.sh
git diff --check

Validate applicable JSON/TOML/shell/schema files.

Verify:
- main SHA unchanged
- ~/stats branch/status unchanged
- no live database access
- no runtime restart
- no real-money change
- no automatic merge

============================================================
CHECKLIST
============================================================

Update docs/AUTOMATION_CHECKLIST.md only for genuinely complete work.

Candidates:
8. builder/reviewer pipeline
9. mechanical validation gates

Do NOT mark runtime supervisor, deployment, planner, daily report, soak test,
or ML Phase 3B complete.

============================================================
END REPORT
============================================================

Report:

1. files changed
2. validation architecture
3. independent reviewer architecture
4. proof builder/reviewer sessions are separate
5. reviewer verdict schema
6. repair-loop behavior
7. fail-closed behavior
8. secret scanning behavior
9. DB/evidence integrity behavior
10. dispatcher integration
11. real builder/reviewer canary
12. autonomous approval count
13. tests/validation
14. main/runtime safety
15. checklist status
16. exact remaining D/E work

DO NOT COMMIT.

Stop and wait for review.
