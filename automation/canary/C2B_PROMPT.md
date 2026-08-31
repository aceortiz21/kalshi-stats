# Automation C2B queue canary

This is one bounded harmless infrastructure canary. Read the repository
automation policies and the durable task/run handoff first. Then create exactly
one file at `automation/runs/c2b-queue-canary-run-001/canary-output.txt` with
exactly this content and one trailing newline:

`AUTOMATION_PHASE_C2B_CANARY_OK`

Do not modify any other tracked file. Do not commit. Do not inspect or access
credentials, private keys, databases, Kalshi, `main`, `/home/aceortiz/stats`,
or `~/stats`. Do not make network requests other than the Codex client's own
service call. Stop immediately if the requested path or branch is not the
isolated task worktree.
