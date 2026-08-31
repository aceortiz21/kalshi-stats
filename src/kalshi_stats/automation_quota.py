"""Host-side rate-limit waiting for one already-selected automation task."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import time
from typing import Any

from .automation_state import (
    ErrorClassification,
    load_run,
    load_task,
    save_run,
    save_task,
    TaskStatus,
    transition_task,
    utc_now,
)

DEFAULT_BACKOFF_SECONDS = (5 * 60, 15 * 60, 30 * 60, 60 * 60)
DEFAULT_MAX_WAIT_SECONDS = 12 * 60 * 60
RunnerCall = Callable[[Any, Any], Mapping[str, Any]]


def _classification(result: Mapping[str, Any]) -> ErrorClassification:
    try:
        return ErrorClassification(result["error_classification"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("runner result must contain a valid error_classification") from exc


def _record_with(run: Any, **changes: Any) -> Any:
    return run.__class__(**{**run.to_dict(), **changes})


def _set_running(task_path: Path, run_path: Path, task: Any, run: Any) -> tuple[Any, Any]:
    if task.status is TaskStatus.QUEUED:
        task = transition_task(
            task,
            TaskStatus.RUNNING,
            next_action="Execute the bounded task runner.",
            updated_at=max(task.updated_at, utc_now()),
        )
    elif task.status is TaskStatus.WAITING_FOR_QUOTA:
        task = transition_task(
            task,
            TaskStatus.RUNNING,
            next_action="Resume the bounded task runner after quota became available.",
            updated_at=max(task.updated_at, utc_now()),
        )
    elif task.status is not TaskStatus.RUNNING:
        raise ValueError(f"quota wrapper requires RUNNING or WAITING_FOR_QUOTA, got {task.status.value}")
    run = _record_with(
        run,
        status=TaskStatus.RUNNING.value,
        finished_at=None,
        error_classification=None,
        next_action=task.next_action,
    )
    save_task(task_path, task)
    save_run(run_path, run)
    return task, run


def _fail_horizon(task_path: Path, run_path: Path, task: Any, run: Any) -> Mapping[str, Any]:
    task = transition_task(
        task,
        TaskStatus.FAILED,
        next_action="Quota wait horizon exceeded; human review required.",
        last_error=ErrorClassification.RATE_LIMITED.value,
        updated_at=max(task.updated_at, utc_now()),
    )
    run = _record_with(
        run,
        status=TaskStatus.FAILED.value,
        error_classification=ErrorClassification.RATE_LIMITED.value,
        next_action="Quota wait horizon exceeded; fail closed.",
    )
    save_task(task_path, task)
    save_run(run_path, run)
    return {"error_classification": ErrorClassification.RATE_LIMITED.value, "status": "FAILED"}


def run_with_quota_wait(
    task_path: str | Path,
    run_path: str | Path,
    run_once: RunnerCall,
    *,
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> Mapping[str, Any]:
    """Delegate bounded invocations, retrying only the runner's RATE_LIMITED result."""

    if not backoff_seconds or any(delay <= 0 for delay in backoff_seconds):
        raise ValueError("backoff_seconds must contain positive delays")
    if max_wait_seconds <= 0:
        raise ValueError("max_wait_seconds must be positive")
    task_file, run_file = Path(task_path), Path(run_path)
    waiting_since: float | None = None
    backoff_index = 0

    while True:
        task, run = load_task(task_file), load_run(run_file)
        wait = run.validation_results.get("quota_wait")
        if task.status is TaskStatus.WAITING_FOR_QUOTA and isinstance(wait, Mapping):
            waiting_since = float(wait["waiting_since"])
            backoff_index = int(wait["backoff_index"])
            retry_at = float(wait["next_retry_at"])
            current = clock()
            if current - waiting_since >= max_wait_seconds:
                return _fail_horizon(task_file, run_file, task, run)
            sleep(max(0.0, retry_at - current))
            task, run = load_task(task_file), load_run(run_file)
            if clock() - waiting_since >= max_wait_seconds:
                return _fail_horizon(task_file, run_file, task, run)

        task, run = _set_running(task_file, run_file, task, run)
        result = run_once(task, run)
        classification = _classification(result)
        if classification is not ErrorClassification.RATE_LIMITED:
            return result

        # The existing runner may have persisted logs, validation, and its own
        # WAITING_FOR_QUOTA RunRecord. Reload before adding quota metadata so
        # the wrapper cannot overwrite that evidence with its stale input.
        task, run = load_task(task_file), load_run(run_file)

        now = clock()
        if waiting_since is None:
            waiting_since = now
        if now - waiting_since >= max_wait_seconds:
            return _fail_horizon(task_file, run_file, task, run)
        delay = backoff_seconds[min(backoff_index, len(backoff_seconds) - 1)]
        validation = dict(run.validation_results)
        validation["quota_wait"] = {
            "waiting_since": waiting_since,
            "next_retry_at": now + delay,
            "backoff_index": backoff_index + 1,
        }
        task = transition_task(
            task,
            TaskStatus.WAITING_FOR_QUOTA,
            next_action="Wait for quota; retry the same attempt at the persisted retry time.",
            last_error=ErrorClassification.RATE_LIMITED.value,
            updated_at=max(task.updated_at, utc_now()),
        )
        run = _record_with(
            run,
            status=TaskStatus.WAITING_FOR_QUOTA.value,
            finished_at=run.finished_at or max(run.started_at, utc_now()),
            error_classification=ErrorClassification.RATE_LIMITED.value,
            validation_results=validation,
            next_action="Wait for quota at the persisted retry time.",
        )
        save_task(task_file, task)
        save_run(run_file, run)
        backoff_index += 1
