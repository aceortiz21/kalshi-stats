# C3 Post-Repair Canary Closeout

Phase C3 is already implemented.

The first independent-review canary returned CHANGES_REQUIRED with two HIGH
findings. Those findings were repaired. Run exactly ONE NEW fresh canary to
verify the repaired implementation.

Do not redesign C3.
Do not implement D, E, research, deployment, runtime supervision, or trading.
Do not merge anything.

PASS requires:

- new builder session
- builder approvals = 0
- all mechanical validation gates PASS
- new reviewer session distinct from builder
- reviewer modifies zero files
- reviewer verdict = PASS
- no blocking CRITICAL/HIGH findings
- secret scan passes
- main unchanged
- ~/stats unchanged
- live DB untouched
- runtime untouched
- real-money execution unchanged

Use new canary/run/evidence IDs. Do not overwrite the prior canary.

If PASS:
- mark checklist items 8 and 9 complete
- update CODEX_HANDOFF with proof

If CHANGES_REQUIRED or BLOCKED:
- preserve evidence
- do not run another real canary
- leave items 8 and 9 incomplete

Run compileall, full pytest, shell checks, schema/config validation, and
git diff --check.

Report builder/reviewer session IDs, approval count, validation result,
reviewer verdict/findings, reviewer modification count, test result, safety
checks, and whether checklist items 8 and 9 are complete.

DO NOT COMMIT.
Stop.
