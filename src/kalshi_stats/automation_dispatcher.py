"""Single-worker durable dispatcher for the Automation V1 task queue."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Iterator, Mapping

from .automation_quota import run_with_quota_wait
from .automation_runner import (
    RunnerConfig,
    execute_launch,
    prepare_launch,
    create_run_directory,
    _write_text_atomic,
)
from .automation_state import (
    ErrorClassification,
    RunRecord,
    TaskPriority,
    TaskRecord,
    TaskStatus,
    load_run,
    load_task,
    save_run,
    save_task,
    transition_task,
    utc_now,
    write_json_atomic,
)
from .automation_worktrees import (
    TaskWorktree,
    create_task_worktree,
    inspect_task_worktree,
)


PRIORITY_ORDER = {TaskPriority.URGENT: 0, TaskPriority.HIGH: 1, TaskPriority.NORMAL: 2, TaskPriority.LOW: 3}
DEFAULT_ROLLOVER_LIMIT = 5


class DispatcherFailure(RuntimeError):
    """A fail-closed dispatcher condition."""


class DispatcherLock:
    """Process lock; kernel release makes stale lock files recoverable."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream = None

    def __enter__(self) -> "DispatcherLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._stream.close()
            self._stream = None
            raise DispatcherFailure("another dispatcher owns the host lock") from exc
        self._stream.seek(0)
        self._stream.truncate()
        self._stream.write(json.dumps({"pid": os.getpid(), "started_at": utc_now()}) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        return self

    def __exit__(self, *_: object) -> None:
        if self._stream is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None


@contextmanager
def dispatcher_lock(path: Path) -> Iterator[DispatcherLock]:
    with DispatcherLock(path) as lock:
        yield lock


def _event(root: Path, event_type: str, task: TaskRecord, **extra: Any) -> None:
    path = root / "automation" / "history" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        json.dump({"type": event_type, "timestamp": utc_now(), "task_id": task.task_id, **extra}, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _task_path(root: Path, task: TaskRecord) -> Path:
    return root / "automation" / "tasks" / f"{task.task_id}.json"


def _dependencies_passed(task: TaskRecord, records: Mapping[str, TaskRecord]) -> bool:
    return all(records.get(dep) is not None and records[dep].status is TaskStatus.PASSED for dep in task.dependencies)


def select_runnable(tasks: list[TaskRecord]) -> TaskRecord | None:
    """Select deterministically by priority, creation time, then task ID."""
    records = {task.task_id: task for task in tasks}
    eligible = [
        task for task in tasks
        if task.status is TaskStatus.QUEUED
        and task.blocked_reason is None
        and task.attempt_count < task.max_attempts
        and _dependencies_passed(task, records)
    ]
    return min(eligible, key=lambda task: (PRIORITY_ORDER[task.priority], task.created_at, task.task_id), default=None)


class Dispatcher:
    def __init__(
        self,
        *,
        repository: Path,
        allowed_worktree_root: Path,
        primary_runtime_worktree: Path,
        base_branch: str,
        auth_directory: Path,
        lock_path: Path | None = None,
        image: str = "kalshi-stats-automation:phase-b-v1",
        timeout_seconds: int = 7200,
        rollover_limit: int = DEFAULT_ROLLOVER_LIMIT,
        runner_call: Callable[[TaskRecord, RunRecord, RunnerConfig], Mapping[str, Any]] | None = None,
    ) -> None:
        self.repository = repository.resolve()
        self.allowed_worktree_root = allowed_worktree_root.resolve()
        self.primary_runtime_worktree = primary_runtime_worktree.resolve()
        self.base_branch = base_branch
        self.auth_directory = auth_directory
        self.lock_path = lock_path or self.repository / "automation" / "state" / "dispatcher.lock"
        self.image = image
        self.timeout_seconds = timeout_seconds
        if rollover_limit < 0:
            raise ValueError("rollover_limit cannot be negative")
        self.rollover_limit = rollover_limit
        self.runner_call = runner_call or self._run_runner

    def _load_tasks(self) -> list[TaskRecord]:
        directory = self.repository / "automation" / "tasks"
        tasks = []
        for path in sorted(directory.glob("*.json")):
            try:
                tasks.append(load_task(path))
            except Exception as exc:
                raise DispatcherFailure(f"invalid task record {path.name}: {exc}") from exc
        return tasks

    def _save_task_everywhere(self, task: TaskRecord) -> None:
        save_task(_task_path(self.repository, task), task)
        worktree_path = Path(task.worktree)
        if worktree_path.is_dir():
            save_task(_task_path(worktree_path, task), task)

    def _recover_ambiguous_running(self, tasks: list[TaskRecord]) -> None:
        for task in tasks:
            if task.status is TaskStatus.RUNNING:
                blocked = transition_task(task, TaskStatus.BLOCKED, next_action="Human review required after ambiguous dispatcher restart.", last_error="ORPHANED_RUNNING_STATE", updated_at=utc_now())
                blocked = replace(blocked, blocked_reason="Previous dispatcher ownership cannot be proven after restart.")
                self._save_task_everywhere(blocked)
                _event(self.repository, "task.blocked", blocked, reason=blocked.blocked_reason)

    def _ensure_worktree(self, task: TaskRecord) -> TaskWorktree:
        if Path(task.worktree).exists():
            return inspect_task_worktree(
                repository=self.repository, base_branch=task.base_branch,
                task_branch=task.branch, worktree_path=Path(task.worktree),
                allowed_worktree_root=self.allowed_worktree_root,
                allowed_base_branches=(self.base_branch, task.base_branch),
                primary_runtime_worktree=self.primary_runtime_worktree,
            )
        return create_task_worktree(
            repository=self.repository, base_branch=task.base_branch,
            task_branch=task.branch, worktree_path=Path(task.worktree),
            allowed_worktree_root=self.allowed_worktree_root,
            allowed_base_branches=(self.base_branch, task.base_branch),
            primary_runtime_worktree=self.primary_runtime_worktree,
        )

    def _prepare_task_inputs(self, task: TaskRecord, worktree: TaskWorktree) -> Path:
        source = Path(task.prompt_path or "")
        if not source.is_absolute():
            source = self.repository / source
        source = source.resolve(strict=True)
        if not source.is_file() or not source.is_relative_to(self.repository):
            raise DispatcherFailure("task specification must be a file inside the repository")
        destination = worktree.path / "automation" / "tasks" / f"{task.task_id}.prompt.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        save_task(_task_path(worktree.path, task), task)
        return destination

    def _new_run(self, task: TaskRecord, run_id: str, *, rollover_count: int = 0, previous_run_id: str | None = None) -> RunRecord:
        return RunRecord(
            run_id=run_id, task_id=task.task_id, status=TaskStatus.RUNNING,
            session_thread_id=None, started_at=utc_now(), finished_at=None,
            branch=task.branch, worktree=task.worktree, files_changed=(),
            validation_results={"dispatcher": {"rollover_count": rollover_count}},
            final_response_path=f"automation/runs/{run_id}/final.md",
            jsonl_log_path=f"automation/runs/{run_id}/events.jsonl",
            error_classification=None,
            next_action="Execute the bounded task through the quota-aware runner.",
            rollover_count=rollover_count, previous_run_id=previous_run_id,
        )

    def _run_runner(self, task: TaskRecord, run: RunRecord, config: RunnerConfig) -> Mapping[str, Any]:
        return execute_launch(prepare_launch(task, run, config))

    def _execute_attempt(self, task: TaskRecord, worktree: TaskWorktree, run: RunRecord, prompt: Path) -> Mapping[str, Any]:
        run_dir = worktree.path / "automation" / "runs" / run.run_id
        config = RunnerConfig(
            allowed_worktree_root=self.allowed_worktree_root,
            primary_runtime_worktree=self.primary_runtime_worktree,
            prompt_path=prompt, run_directory=run_dir, auth_directory=self.auth_directory,
            image=self.image, timeout_seconds=self.timeout_seconds,
        )
        return run_with_quota_wait(
            _task_path(worktree.path, task), run_dir / "state.json",
            lambda current_task, current_run: self.runner_call(current_task, current_run, config),
            on_wait=lambda waiting_task, waiting_run: _event(
                self.repository, "quota.wait", waiting_task, run_id=waiting_run.run_id,
                next_retry_at=waiting_run.validation_results.get("quota_wait", {}).get("next_retry_at"),
            ),
        )

    def _finish(self, task: TaskRecord, run: RunRecord, classification: ErrorClassification) -> TaskRecord:
        if classification is ErrorClassification.SUCCESS:
            # C2B has no independent reviewer, but terminal state still follows
            # the durable transition graph rather than bypassing it.
            final_task = transition_task(task, TaskStatus.VALIDATING, next_action="Record the successful runner result.")
            final_task = transition_task(final_task, TaskStatus.REVIEWING, next_action="Review the bounded runner evidence.")
            final_task = transition_task(final_task, TaskStatus.PASSED, next_action="Task passed; preserve evidence for human review.")
            final_run = replace(load_run(Path(task.worktree) / "automation" / "runs" / run.run_id / "state.json"), status=TaskStatus.PASSED, finished_at=utc_now(), error_classification=classification, next_action="Task passed.")
        elif classification in {ErrorClassification.SECURITY_VIOLATION, ErrorClassification.DATABASE_INTEGRITY_FAILURE}:
            final_task = transition_task(task, TaskStatus.BLOCKED, next_action="Human investigation required; automation stopped.", last_error=classification.value)
            final_task = replace(final_task, blocked_reason=classification.value)
            final_run = replace(load_run(Path(task.worktree) / "automation" / "runs" / run.run_id / "state.json"), status=TaskStatus.BLOCKED, error_classification=classification, next_action="Blocked fail closed.")
        else:
            final_task = transition_task(task, TaskStatus.FAILED, next_action="Preserve evidence for human review.", last_error=classification.value)
            final_run = replace(load_run(Path(task.worktree) / "automation" / "runs" / run.run_id / "state.json"), status=TaskStatus.FAILED, error_classification=classification, next_action="Task failed; human review required.")
        save_run(Path(task.worktree) / "automation" / "runs" / run.run_id / "state.json", final_run)
        self._save_task_everywhere(final_task)
        _event(self.repository, f"task.{final_task.status.value.lower()}", final_task, run_id=run.run_id, classification=classification.value)
        return final_task

    def process_task(self, task: TaskRecord) -> TaskRecord:
        worktree = self._ensure_worktree(task)
        prompt = self._prepare_task_inputs(task, worktree)
        if task.status is TaskStatus.QUEUED:
            task = transition_task(task, TaskStatus.RUNNING, next_action="Execute the bounded task through the quota-aware runner.")
            run_id = f"{task.task_id}-run-{task.attempt_count}"
            task = replace(task, run_ids=task.run_ids + (run_id,), report_paths=task.report_paths + (f"automation/runs/{run_id}",), current_run_id=run_id)
            self._save_task_everywhere(task)
            _event(self.repository, "task.claimed", task, run_id=run_id)
        else:
            run_id = task.current_run_id or task.run_ids[-1]
        run_path = worktree.path / "automation" / "runs" / run_id / "state.json"
        run = load_run(run_path) if run_path.exists() else self._new_run(task, run_id)
        if not run_path.exists():
            create_run_directory(run_path.parent, task, run)
            save_run(run_path, run)
            _event(self.repository, "run.started", task, run_id=run_id)
        while True:
            result = self._execute_attempt(task, worktree, run, prompt)
            classification = ErrorClassification(result.get("error_classification", ErrorClassification.UNKNOWN_FAILURE.value))
            if classification is ErrorClassification.CONTEXT_EXHAUSTED:
                if run.rollover_count >= self.rollover_limit:
                    return self._finish(task, run, ErrorClassification.UNKNOWN_FAILURE)
                next_rollover = run.rollover_count + 1
                handoff = (worktree.path / "automation" / "runs" / run.run_id / "HANDOFF.md")
                _write_text_atomic(handoff, f"# Context Rollover\n\nTask: `{task.task_id}`\nPrevious run: `{run.run_id}`\nContinuation count: {next_rollover}\n\nReconstruct from AGENTS.md, README.md, docs/RESEARCH_SYSTEM.md, docs/CODEX_HANDOFF.md, docs/AUTOMATION_CHECKLIST.md, the canonical task JSON, this HANDOFF.md, git status/diff/log, validation.json, and the previous result classification `{classification.value}`.\n")
                new_id = f"{task.task_id}-run-{task.attempt_count}-context-{next_rollover}"
                new_run = self._new_run(task, new_id, rollover_count=next_rollover, previous_run_id=run.run_id)
                create_run_directory(worktree.path / "automation" / "runs" / new_id, task, new_run)
                _write_text_atomic(
                    worktree.path / "automation" / "runs" / new_id / "HANDOFF.md",
                    handoff.read_text(encoding="utf-8")
                    + f"\nFresh continuation run: `{new_id}`. Do not rely on prior chat context.\n",
                )
                task = replace(task, run_ids=task.run_ids + (new_id,), report_paths=task.report_paths + (f"automation/runs/{new_id}",), current_run_id=new_id, updated_at=utc_now(), next_action="Continue the same task from the compact handoff.")
                self._save_task_everywhere(task)
                save_run(worktree.path / "automation" / "runs" / new_id / "state.json", new_run)
                _event(self.repository, "context.rollover", task, previous_run_id=run.run_id, run_id=new_id)
                run = new_run
                continue
            return self._finish(task, run, classification)

    def run(self, *, once: bool = False, continuous: bool = False, poll_seconds: float = 5.0) -> list[TaskRecord]:
        processed: list[TaskRecord] = []
        with dispatcher_lock(self.lock_path):
            while True:
                tasks = self._load_tasks()
                self._recover_ambiguous_running(tasks)
                tasks = self._load_tasks()
                waiting = next((task for task in tasks if task.status is TaskStatus.WAITING_FOR_QUOTA), None)
                task = waiting or select_runnable(tasks)
                if task is None:
                    if continuous:
                        time.sleep(poll_seconds)
                        continue
                    return processed
                _event(self.repository, "task.dispatch", task)
                processed.append(self.process_task(task))
                if once:
                    return processed
