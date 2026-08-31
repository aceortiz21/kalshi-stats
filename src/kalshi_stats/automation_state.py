"""Durable state primitives for the Automation V1 control plane.

This module deliberately contains no task execution, retry loop, Codex invocation,
runtime supervision, or Git mutation.  It defines and persists the state that later
automation phases may consume.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    REVIEWING = "REVIEWING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    WAITING_FOR_QUOTA = "WAITING_FOR_QUOTA"
    ARCHIVED = "ARCHIVED"


class TaskSource(str, Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"


class TaskPriority(str, Enum):
    URGENT = "URGENT"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class ErrorClassification(str, Enum):
    SUCCESS = "SUCCESS"
    RATE_LIMITED = "RATE_LIMITED"
    CONTEXT_EXHAUSTED = "CONTEXT_EXHAUSTED"
    CODE_FAILURE = "CODE_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    DATABASE_INTEGRITY_FAILURE = "DATABASE_INTEGRITY_FAILURE"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


ALLOWED_TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.ARCHIVED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.VALIDATING,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.WAITING_FOR_QUOTA,
        }
    ),
    TaskStatus.VALIDATING: frozenset(
        {TaskStatus.REVIEWING, TaskStatus.FAILED, TaskStatus.BLOCKED}
    ),
    TaskStatus.REVIEWING: frozenset(
        {TaskStatus.PASSED, TaskStatus.FAILED, TaskStatus.BLOCKED}
    ),
    TaskStatus.PASSED: frozenset({TaskStatus.ARCHIVED}),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED, TaskStatus.ARCHIVED}),
    TaskStatus.BLOCKED: frozenset({TaskStatus.QUEUED, TaskStatus.ARCHIVED}),
    TaskStatus.WAITING_FOR_QUOTA: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.ARCHIVED,
        }
    ),
    TaskStatus.ARCHIVED: frozenset(),
}


REQUIRED_CONTEXT_RECOVERY_INPUTS = (
    "AGENTS.md",
    "README.md",
    "docs/RESEARCH_SYSTEM.md",
    "docs/CODEX_HANDOFF.md",
    "docs/AUTOMATION_CHECKLIST.md",
    "task JSON",
    "current run HANDOFF.md",
    "git status",
    "git diff",
    "git log",
    "prior validation results",
)


_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TASK_BRANCH_PATTERN = re.compile(
    r"^(?:auto|automation)/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$"
)
_AUTONOMOUS_INTEGRATION_TARGET = "automation-integration"


def utc_now() -> str:
    """Return a stable, timezone-aware UTC timestamp for persisted state."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_timestamp(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def is_valid_task_branch(branch: str) -> bool:
    """Return whether a branch may host ordinary autonomous task/run work."""

    return (
        isinstance(branch, str)
        and _TASK_BRANCH_PATTERN.fullmatch(branch) is not None
    )


def is_valid_autonomous_integration_target(branch: str) -> bool:
    """Return whether a branch is the permitted autonomous merge destination."""

    return branch == _AUTONOMOUS_INTEGRATION_TARGET


def _validate_task_branch(branch: str) -> None:
    if not is_valid_task_branch(branch):
        raise ValueError(
            "task branch must be auto/<task-name> or automation/<task-name>; "
            "main and automation-integration are not task work branches"
        )


def _coerce_status(value: TaskStatus | str) -> TaskStatus:
    try:
        return value if isinstance(value, TaskStatus) else TaskStatus(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid task status: {value!r}") from exc


def _coerce_error_classification(
    value: ErrorClassification | str | None,
) -> ErrorClassification | None:
    if value is None or isinstance(value, ErrorClassification):
        return value
    try:
        return ErrorClassification(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid error classification: {value!r}") from exc


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    title: str
    objective: str
    status: TaskStatus
    prerequisites: tuple[str, ...]
    branch: str
    worktree: str
    attempt_count: int
    max_attempts: int
    created_at: str
    updated_at: str
    run_ids: tuple[str, ...]
    report_paths: tuple[str, ...]
    last_error: str | None
    next_action: str
    source: TaskSource = TaskSource.USER
    priority: TaskPriority = TaskPriority.NORMAL
    prompt_path: str | None = None
    base_branch: str = "automation/phase-c2b-v1"
    current_run_id: str | None = None
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _coerce_status(self.status))
        object.__setattr__(self, "source", self.source if isinstance(self.source, TaskSource) else TaskSource(self.source))
        object.__setattr__(self, "priority", self.priority if isinstance(self.priority, TaskPriority) else TaskPriority(self.priority))
        object.__setattr__(self, "prerequisites", tuple(self.prerequisites))
        object.__setattr__(self, "run_ids", tuple(self.run_ids))
        object.__setattr__(self, "report_paths", tuple(self.report_paths))
        _require_text(self.task_id, "task_id")
        if not _TASK_ID_PATTERN.fullmatch(self.task_id):
            raise ValueError("task_id contains unsupported characters")
        _require_text(self.title, "title")
        _require_text(self.objective, "objective")
        _validate_task_branch(self.branch)
        _require_text(self.base_branch, "base_branch")
        if self.base_branch in {"main", "automation-integration"}:
            raise ValueError("base_branch cannot be main or automation-integration")
        _require_text(self.worktree, "worktree")
        _require_text(self.next_action, "next_action")
        if self.attempt_count < 0:
            raise ValueError("attempt_count cannot be negative")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt_count cannot exceed max_attempts")
        created = _validate_timestamp(self.created_at, "created_at")
        updated = _validate_timestamp(self.updated_at, "updated_at")
        if updated < created:
            raise ValueError("updated_at cannot precede created_at")
        for field_name, values in (
            ("prerequisites", self.prerequisites),
            ("run_ids", self.run_ids),
            ("report_paths", self.report_paths),
        ):
            if any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"{field_name} entries must be non-empty strings")
        if self.last_error is not None and not isinstance(self.last_error, str):
            raise ValueError("last_error must be a string or null")
        if self.prompt_path is not None:
            _require_text(self.prompt_path, "prompt_path")
        if self.current_run_id is not None and not _TASK_ID_PATTERN.fullmatch(self.current_run_id):
            raise ValueError("current_run_id contains unsupported characters")
        if self.blocked_reason is not None and not isinstance(self.blocked_reason, str):
            raise ValueError("blocked_reason must be a string or null")

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        title: str,
        objective: str,
        branch: str,
        worktree: str,
        prerequisites: tuple[str, ...] = (),
        dependencies: tuple[str, ...] | None = None,
        max_attempts: int = 3,
        next_action: str = "Start task when prerequisites are satisfied.",
        source: TaskSource | str = TaskSource.USER,
        priority: TaskPriority | str = TaskPriority.NORMAL,
        prompt_path: str | None = None,
        base_branch: str = "automation/phase-c2b-v1",
        now: str | None = None,
    ) -> TaskRecord:
        timestamp = now or utc_now()
        return cls(
            task_id=task_id,
            title=title,
            objective=objective,
            status=TaskStatus.QUEUED,
            prerequisites=tuple(dependencies if dependencies is not None else prerequisites),
            branch=branch,
            worktree=worktree,
            attempt_count=0,
            max_attempts=max_attempts,
            created_at=timestamp,
            updated_at=timestamp,
            run_ids=(),
            report_paths=(),
            last_error=None,
            next_action=next_action,
            source=source,
            priority=priority,
            prompt_path=prompt_path,
            base_branch=base_branch,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["source"] = self.source.value
        payload["priority"] = self.priority.value
        for field_name in ("prerequisites", "run_ids", "report_paths"):
            payload[field_name] = list(payload[field_name])
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskRecord:
        values = dict(payload)
        # C1 records used prerequisites; C2B calls the same field dependencies.
        if "prerequisites" not in values and "dependencies" in values:
            values["prerequisites"] = values["dependencies"]
        if "source" not in values:
            values["source"] = TaskSource.USER.value
        if "priority" not in values:
            values["priority"] = TaskPriority.NORMAL.value
        if "base_branch" not in values:
            values["base_branch"] = "automation/phase-c2b-v1"
        values.setdefault("prompt_path", None)
        values.setdefault("current_run_id", values.get("run_ids", [None])[-1] if values.get("run_ids") else None)
        values.setdefault("blocked_reason", None)
        values.pop("dependencies", None)
        return cls(**values)

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.prerequisites


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    task_id: str
    status: TaskStatus
    session_thread_id: str | None
    started_at: str
    finished_at: str | None
    branch: str
    worktree: str
    files_changed: tuple[str, ...]
    validation_results: Mapping[str, Any]
    final_response_path: str
    jsonl_log_path: str
    error_classification: ErrorClassification | None
    next_action: str
    rollover_count: int = 0
    previous_run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _coerce_status(self.status))
        object.__setattr__(
            self,
            "error_classification",
            _coerce_error_classification(self.error_classification),
        )
        object.__setattr__(self, "files_changed", tuple(self.files_changed))
        object.__setattr__(self, "validation_results", dict(self.validation_results))
        for field_name, value in (("run_id", self.run_id), ("task_id", self.task_id)):
            _require_text(value, field_name)
            if not _TASK_ID_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} contains unsupported characters")
        _validate_task_branch(self.branch)
        for field_name, value in (
            ("worktree", self.worktree),
            ("final_response_path", self.final_response_path),
            ("jsonl_log_path", self.jsonl_log_path),
            ("next_action", self.next_action),
        ):
            _require_text(value, field_name)
        started = _validate_timestamp(self.started_at, "started_at")
        if self.finished_at is not None:
            finished = _validate_timestamp(self.finished_at, "finished_at")
            if finished < started:
                raise ValueError("finished_at cannot precede started_at")
        if self.session_thread_id is not None and not self.session_thread_id:
            raise ValueError("session_thread_id must be non-empty or null")
        if any(not isinstance(path, str) or not path for path in self.files_changed):
            raise ValueError("files_changed entries must be non-empty strings")
        if self.rollover_count < 0:
            raise ValueError("rollover_count cannot be negative")
        if self.previous_run_id is not None and not _TASK_ID_PATTERN.fullmatch(self.previous_run_id):
            raise ValueError("previous_run_id contains unsupported characters")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["files_changed"] = list(payload["files_changed"])
        if self.error_classification is not None:
            payload["error_classification"] = self.error_classification.value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunRecord:
        return cls(**dict(payload))


def transition_task(
    task: TaskRecord,
    new_status: TaskStatus | str,
    *,
    next_action: str,
    last_error: str | None = None,
    updated_at: str | None = None,
) -> TaskRecord:
    """Return a validated next task state without mutating the input record."""

    target = _coerce_status(new_status)
    if target not in ALLOWED_TASK_TRANSITIONS[task.status]:
        raise ValueError(f"transition {task.status.value} -> {target.value} is not allowed")
    attempt_count = task.attempt_count
    if task.status == TaskStatus.QUEUED and target == TaskStatus.RUNNING:
        if attempt_count >= task.max_attempts:
            raise ValueError("task has exhausted max_attempts")
        attempt_count += 1
    return replace(
        task,
        status=target,
        attempt_count=attempt_count,
        updated_at=updated_at or utc_now(),
        last_error=last_error,
        next_action=next_action,
    )


def write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace a JSON file using a temporary file in its directory."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def save_task(path: str | Path, task: TaskRecord) -> None:
    write_json_atomic(path, task.to_dict())


def load_task(path: str | Path) -> TaskRecord:
    with Path(path).open(encoding="utf-8") as source:
        return TaskRecord.from_dict(json.load(source))


def save_run(path: str | Path, run: RunRecord) -> None:
    write_json_atomic(path, run.to_dict())


def load_run(path: str | Path) -> RunRecord:
    with Path(path).open(encoding="utf-8") as source:
        return RunRecord.from_dict(json.load(source))
