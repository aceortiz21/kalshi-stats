# Safety Policy

Policy ID: `AUTOMATION_SAFETY_V1`

Applies to: every automated task, run, process, and future container

Default on uncertainty: stop without mutation

| Rule ID | Requirement |
| --- | --- |
| SAFE-001 | Live-money Kalshi execution MUST remain disabled. Automation MUST NOT submit real orders or enable a live execution switch. |
| SAFE-002 | Automation MUST NOT receive, request, print, persist, or use write-capable Kalshi credentials. |
| SAFE-003 | Automated processes MUST NOT access host secrets, private keys, credential stores, or unrelated environment credentials. |
| SAFE-004 | Historical, paper, validation, and negative evidence MUST NOT be deleted, reset, or rewritten to improve results. |
| SAFE-005 | Dependency changes MUST be declared, pinned or bounded as appropriate, and reproducible from repository files. |
| SAFE-006 | A credential-boundary violation MUST stop the automated process and be classified `SECURITY_VIOLATION`; it MUST NOT retry automatically. |
| SAFE-007 | A database-integrity violation MUST stop the automated process and be classified `DATABASE_INTEGRITY_FAILURE`; the affected data MUST be preserved for investigation. |
| SAFE-008 | Logs, state files, reports, and handoffs MUST NOT contain secrets. |

Phase A defines these stop conditions but does not implement the future process
supervisor or credential/database-integrity scanners.
