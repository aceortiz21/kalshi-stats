# Research Policy

Policy ID: `AUTOMATION_RESEARCH_V1`

Applies to: automated research, validation, reporting, and challenger creation

Default on uncertainty: preserve ambiguity and report insufficient evidence

| Rule ID | Requirement |
| --- | --- |
| RES-001 | Historical and prospective evaluation MUST preserve chronology. A decision may use only information available at its timestamp. |
| RES-002 | Settlement outcomes MUST NOT be consumed until the simulated or observed path reaches settlement. |
| RES-003 | Discovery, historical screening, holdout, walk-forward, prospective paper, and future execution evidence MUST remain distinguishable. |
| RES-004 | A challenger MUST have an immutable definition, unique key/version, creation timestamp, discovery cutoff, and fresh forward-only start timestamp. |
| RES-005 | Negative and null results MUST be preserved and reported. Lack of demonstrated edge is a valid outcome. |
| RES-006 | Ambiguous historical paths MUST remain ambiguous. Automation MUST NOT invent TP/SL ordering, fills, prices, labels, or outcomes. |
| RES-007 | Previously tested frozen definitions MUST NOT be rewritten because their results are unfavorable. A changed hypothesis becomes a new version. |
| RES-008 | Historical success MUST NOT be presented as prospective proof or promoted directly to execution eligibility. |
| RES-009 | ML Phase 3B MUST remain paused until checklist item 16 is explicitly reached through automation. |

Automation optimizes for valid, reproducible evidence—not a profitable-looking
result. Real-money execution authority is outside this research control plane.
