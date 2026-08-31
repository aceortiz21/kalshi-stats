"""Bounded builder -> validation -> independent-review coordination."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .automation_review import ReviewResult, ReviewerVerdict, save_review
from .automation_state import ErrorClassification, RunRecord, TaskRecord
from .automation_validation import ValidationReport, worktree_snapshot


DEFAULT_MAX_REPAIR_CYCLES = 2
NON_REPAIRABLE = frozenset(
    {ErrorClassification.SECURITY_VIOLATION, ErrorClassification.DATABASE_INTEGRITY_FAILURE}
)


@dataclass(frozen=True)
class PipelineDecision:
    status: str
    classification: ErrorClassification
    builder_runs: tuple[RunRecord, ...]
    reviews: tuple[ReviewResult, ...]
    validations: tuple[ValidationReport, ...]
    repair_cycles: int
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "classification": self.classification.value,
            "builder_runs": [run.run_id for run in self.builder_runs],
            "builder_sessions": [run.session_thread_id for run in self.builder_runs],
            "reviewer_runs": [review.reviewer_run_id for review in self.reviews],
            "reviewer_sessions": [review.reviewer_session_id for review in self.reviews],
            "reviewer_verdicts": [review.verdict.value for review in self.reviews],
            "repair_cycles": self.repair_cycles,
            "reason": self.reason,
        }


ValidationCall = Callable[[TaskRecord, RunRecord, int], ValidationReport]
ReviewCall = Callable[[TaskRecord, RunRecord, ValidationReport, int], ReviewResult]
RepairCall = Callable[[TaskRecord, ReviewResult, int], tuple[RunRecord, ErrorClassification]]


def run_review_pipeline(
    task: TaskRecord,
    builder_run: RunRecord,
    *,
    validate: ValidationCall,
    review: ReviewCall,
    repair: RepairCall,
    evidence_directory: Path,
    max_repair_cycles: int = DEFAULT_MAX_REPAIR_CYCLES,
) -> PipelineDecision:
    """Run bounded independent review; launchers remain quota/context owners."""

    if max_repair_cycles < 0:
        raise ValueError("max_repair_cycles cannot be negative")
    builders = [builder_run]
    validations: list[ValidationReport] = []
    reviews: list[ReviewResult] = []
    current = builder_run
    for cycle in range(max_repair_cycles + 1):
        validation = validate(task, current, cycle)
        validations.append(validation)
        if not validation.passed:
            classification = next(
                (
                    ErrorClassification(gate.classification)
                    for gate in validation.gates
                    if gate.required and not gate.passed and gate.classification
                ),
                ErrorClassification.TEST_FAILURE,
            )
            status = "BLOCKED" if classification in NON_REPAIRABLE else "FAILED"
            return PipelineDecision(status, classification, tuple(builders), tuple(reviews), tuple(validations), cycle, "required mechanical gate failed")

        before = worktree_snapshot(Path(task.worktree))
        result = review(task, current, validation, cycle)
        after = worktree_snapshot(Path(task.worktree))
        changed_by_review = {
            name for name in before.keys() | after.keys()
            if before.get(name) != after.get(name)
        }
        allowed_prefixes = (
            f"automation/runs/{result.reviewer_run_id}/",
        )
        allowed_exact = {
            f"automation/tasks/{result.reviewer_run_id}.prompt.md",
            f"automation/tasks/{task.task_id}.json",
        }
        unexpected = {
            name for name in changed_by_review
            if name not in allowed_exact
            and not any(name.startswith(prefix) for prefix in allowed_prefixes)
        }
        if unexpected:
            return PipelineDecision("BLOCKED", ErrorClassification.SECURITY_VIOLATION, tuple(builders), tuple(reviews), tuple(validations), cycle, "reviewer modified the task worktree")
        if result.builder_run_id != current.run_id:
            raise ValueError("review result references the wrong builder run")
        if result.builder_session_id != current.session_thread_id:
            raise ValueError("review result references the wrong builder session")
        reviews.append(result)
        save_review(evidence_directory / f"review-{cycle}.json", result)
        if result.verdict is ReviewerVerdict.PASS:
            return PipelineDecision("PASSED", ErrorClassification.SUCCESS, tuple(builders), tuple(reviews), tuple(validations), cycle)
        if result.verdict is ReviewerVerdict.BLOCKED:
            return PipelineDecision("BLOCKED", ErrorClassification.UNKNOWN_FAILURE, tuple(builders), tuple(reviews), tuple(validations), cycle, "independent reviewer blocked the task")
        if cycle >= max_repair_cycles:
            return PipelineDecision("FAILED", ErrorClassification.CODE_FAILURE, tuple(builders), tuple(reviews), tuple(validations), cycle, "maximum reviewer repair cycles exhausted")
        repaired, classification = repair(task, result, cycle + 1)
        if classification in NON_REPAIRABLE:
            return PipelineDecision("BLOCKED", classification, tuple(builders), tuple(reviews), tuple(validations), cycle, "non-repairable safety failure")
        if classification is not ErrorClassification.SUCCESS:
            return PipelineDecision("FAILED", classification, tuple(builders), tuple(reviews), tuple(validations), cycle, "fresh builder repair failed")
        if not repaired.session_thread_id or repaired.session_thread_id in {
            current.session_thread_id,
            result.reviewer_session_id,
        }:
            return PipelineDecision("BLOCKED", ErrorClassification.SECURITY_VIOLATION, tuple(builders), tuple(reviews), tuple(validations), cycle, "repair builder session was not fresh")
        builders.append(repaired)
        current = repaired
    raise AssertionError("bounded loop must return")
