"""Structured independent-review records and prompt construction for Phase C3."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping

from .automation_runner import redact_diagnostic_text
from .automation_state import TaskRecord, write_json_atomic


class ReviewerVerdict(str, Enum):
    PASS = "PASS"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    BLOCKED = "BLOCKED"


class FindingSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class ReviewFinding:
    severity: FindingSeverity
    code: str
    summary: str
    detail: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReviewFinding":
        return cls(
            severity=FindingSeverity(payload["severity"]),
            code=str(payload["code"]),
            summary=redact_diagnostic_text(str(payload["summary"])),
            detail=redact_diagnostic_text(str(payload["detail"])),
        )


@dataclass(frozen=True)
class ReviewResult:
    verdict: ReviewerVerdict
    findings: tuple[ReviewFinding, ...]
    reviewer_run_id: str
    reviewer_session_id: str
    builder_run_id: str
    builder_session_id: str
    repair_cycle: int

    def __post_init__(self) -> None:
        if not self.reviewer_session_id or not self.builder_session_id:
            raise ValueError("builder and reviewer session identifiers are required")
        if self.reviewer_session_id == self.builder_session_id:
            raise ValueError("reviewer must use a session distinct from the builder")
        if self.repair_cycle < 0:
            raise ValueError("repair_cycle cannot be negative")
        if self.verdict is ReviewerVerdict.PASS and any(
            finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.HIGH}
            for finding in self.findings
        ):
            raise ValueError("PASS cannot contain a blocking finding")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        payload["findings"] = [
            {**asdict(finding), "severity": finding.severity.value}
            for finding in self.findings
        ]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReviewResult":
        return cls(
            verdict=ReviewerVerdict(payload["verdict"]),
            findings=tuple(ReviewFinding.from_dict(item) for item in payload.get("findings", ())),
            reviewer_run_id=str(payload["reviewer_run_id"]),
            reviewer_session_id=str(payload["reviewer_session_id"]),
            builder_run_id=str(payload["builder_run_id"]),
            builder_session_id=str(payload["builder_session_id"]),
            repair_cycle=int(payload.get("repair_cycle", 0)),
        )


def parse_reviewer_output(
    text: str,
    *,
    reviewer_run_id: str,
    reviewer_session_id: str,
    builder_run_id: str,
    builder_session_id: str,
    repair_cycle: int,
) -> ReviewResult:
    """Parse the reviewer's JSON-only final response and bind trusted IDs."""

    payload = json.loads(text)
    if not isinstance(payload, dict) or set(payload) - {"verdict", "findings"}:
        raise ValueError("reviewer output must be one verdict object")
    return ReviewResult(
        verdict=ReviewerVerdict(payload["verdict"]),
        findings=tuple(ReviewFinding.from_dict(item) for item in payload.get("findings", ())),
        reviewer_run_id=reviewer_run_id,
        reviewer_session_id=reviewer_session_id,
        builder_run_id=builder_run_id,
        builder_session_id=builder_session_id,
        repair_cycle=repair_cycle,
    )


def reviewer_prompt(
    task: TaskRecord,
    *,
    builder_run_id: str,
    validation_path: str,
    diff_text: str,
) -> str:
    """Build a durable, read-only review brief with all required evidence."""

    safe_diff = redact_diagnostic_text(diff_text)
    return f"""Act only as the independent reviewer for task `{task.task_id}`.
Do not edit, create, delete, or format any worktree file. Reconstruct context from the
repository policies, task specification, Git status/diff/log, changed code/tests, and
mechanical validation evidence at `{validation_path}`. Builder run: `{builder_run_id}`.

Task specification:
{task.objective}

Current diff:
```diff
{safe_diff}
```

Return only JSON matching:
{{"verdict":"PASS|CHANGES_REQUIRED|BLOCKED","findings":[{{"severity":"CRITICAL|HIGH|MEDIUM|LOW","code":"stable-code","summary":"concise","detail":"actionable"}}]}}
A PASS is forbidden unless every required validation gate passed and no blocking finding remains.
"""


def repair_prompt(task: TaskRecord, review: ReviewResult) -> str:
    findings = json.dumps([asdict(item) | {"severity": item.severity.value} for item in review.findings], indent=2)
    return f"""Perform one bounded builder repair for task `{task.task_id}`.
Use a fresh session. Preserve all prior evidence and make only changes required by the
independent reviewer. Do not merge, deploy, access live data, or enable real money.

Original task specification:
{task.objective}

Exact reviewer findings:
{findings}
"""


def save_review(path: Path, review: ReviewResult) -> None:
    write_json_atomic(path, review.to_dict())
