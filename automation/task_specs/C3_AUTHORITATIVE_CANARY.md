# C3 Authoritative Pipeline Canary

Perform one harmless bounded builder task.

Create exactly this file:

automation/runs/phase-c3-authoritative-canary-run-1/canary-output.txt

Its exact contents must be:

AUTOMATION_C3_AUTHORITATIVE_CANARY_OK

followed by one newline.

Do not edit tracked source code.
Do not edit documentation.
Do not run another Codex process.
Do not invoke a reviewer.
Do not invoke Docker.
Do not attempt host coordination.
Do not access the primary runtime or live databases.
Do not change real-money functionality.

The host automation dispatcher is responsible for mechanical validation and
independent review after this builder exits.

After creating the exact file, verify its contents and stop.

DO NOT COMMIT.
