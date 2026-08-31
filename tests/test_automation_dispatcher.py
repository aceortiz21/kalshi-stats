import json
from pathlib import Path
import subprocess

import pytest

from kalshi_stats.automation_dispatcher import Dispatcher, DispatcherFailure, DispatcherLock, select_runnable
from kalshi_stats.automation_state import TaskPriority, TaskRecord, TaskStatus, load_task, load_run, save_task, save_run, RunRecord


NOW = "2026-08-31T12:00:00Z"


def _task(task_id, root, *, priority=TaskPriority.NORMAL, dependencies=()):
    return TaskRecord.create(
        task_id=task_id, title=task_id, objective="bounded fixture objective",
        branch=f"automation/{task_id}", worktree=str(root / "tasks" / task_id),
        dependencies=dependencies, priority=priority, prompt_path="spec.md",
        base_branch="automation/base", now=NOW,
    )


def test_selection_is_priority_then_creation_then_id(tmp_path):
    tasks = [_task("b", tmp_path, priority=TaskPriority.NORMAL), _task("a", tmp_path, priority=TaskPriority.HIGH)]
    assert select_runnable(tasks).task_id == "a"
    blocked = _task("blocked", tmp_path, dependencies=("missing",))
    assert select_runnable([blocked]) is None
    failed = TaskRecord(**{**_task("failed", tmp_path).to_dict(), "status": "FAILED"})
    dependent = _task("dependent", tmp_path, dependencies=("failed",))
    assert select_runnable([failed, dependent]) is None


def test_lock_rejects_second_owner(tmp_path):
    path = tmp_path / "dispatcher.lock"
    first = DispatcherLock(path)
    first.__enter__()
    try:
        with pytest.raises(DispatcherFailure):
            DispatcherLock(path).__enter__()
    finally:
        first.__exit__(None, None, None)


@pytest.fixture
def dispatcher_fixture(tmp_path):
    repo = tmp_path / "repo"
    root = tmp_path / "tasks"
    repo.mkdir(); root.mkdir()
    subprocess.run(("git", "init", "-b", "automation/base", str(repo)), check=True, capture_output=True)
    def git(*args):
        return subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True, text=True)
    git("config", "user.name", "test"); git("config", "user.email", "test@invalid")
    (repo / "spec.md").write_text("do bounded fixture\n", encoding="utf-8")
    (repo / ".gitignore").write_text("automation/tasks/*.prompt.md\nautomation/runs/\n", encoding="utf-8")
    git("add", "."); git("commit", "-m", "fixture")
    (repo / "automation" / "tasks").mkdir(parents=True)
    (repo / "automation" / "runs").mkdir(parents=True)
    auth = tmp_path / "auth"; auth.mkdir()
    return repo, root, auth


def test_dispatcher_chains_dependency_and_once_processes_one(dispatcher_fixture):
    repo, root, auth = dispatcher_fixture
    a = _task("a", repo.parent, priority=TaskPriority.HIGH)
    b = _task("b", repo.parent, dependencies=("a",))
    # Fixture paths are intentionally rewritten to the configured narrow root.
    a = TaskRecord(**{**a.to_dict(), "worktree": str(root / "a"), "base_branch": "automation/base"})
    b = TaskRecord(**{**b.to_dict(), "worktree": str(root / "b"), "base_branch": "automation/base"})
    save_task(repo / "automation" / "tasks" / "a.json", a)
    save_task(repo / "automation" / "tasks" / "b.json", b)
    calls = []
    def fake(task, run, config):
        calls.append((task.task_id, run.run_id, task.attempt_count))
        state_path = Path(task.worktree) / "automation" / "runs" / run.run_id / "state.json"
        save_run(state_path, RunRecord.from_dict({**run.to_dict(), "status": "VALIDATING", "error_classification": None}))
        return {"error_classification": "SUCCESS"}
    dispatcher = Dispatcher(repository=repo, allowed_worktree_root=root, primary_runtime_worktree=repo,
                            base_branch="automation/base", auth_directory=auth, runner_call=fake)
    result = dispatcher.run(once=True)
    assert [task.task_id for task in result] == ["a"]
    assert calls == [("a", "a-run-1", 1)]
    assert load_task(repo / "automation" / "tasks" / "a.json").status is TaskStatus.PASSED
    assert load_task(repo / "automation" / "tasks" / "b.json").status is TaskStatus.QUEUED
    dispatcher.run()
    assert load_task(repo / "automation" / "tasks" / "b.json").status is TaskStatus.PASSED


def test_context_rollover_keeps_attempt_and_creates_continuation(dispatcher_fixture):
    repo, root, auth = dispatcher_fixture
    task = _task("context", repo.parent)
    task = TaskRecord(**{**task.to_dict(), "worktree": str(root / "context"), "base_branch": "automation/base"})
    save_task(repo / "automation" / "tasks" / "context.json", task)
    def fake(current, run, config):
        state_path = Path(current.worktree) / "automation" / "runs" / run.run_id / "state.json"
        classification = "CONTEXT_EXHAUSTED" if run.rollover_count == 0 else "SUCCESS"
        save_run(state_path, RunRecord.from_dict({**run.to_dict(), "status": "FAILED" if classification != "SUCCESS" else "VALIDATING", "error_classification": classification}))
        return {"error_classification": classification}
    dispatcher = Dispatcher(repository=repo, allowed_worktree_root=root, primary_runtime_worktree=repo,
                            base_branch="automation/base", auth_directory=auth, runner_call=fake)
    dispatcher.run(once=True)
    final = load_task(repo / "automation" / "tasks" / "context.json")
    assert final.status is TaskStatus.PASSED
    assert final.attempt_count == 1
    assert len(final.run_ids) == 2
    continuation = load_run(Path(final.worktree) / "automation" / "runs" / final.run_ids[-1] / "state.json")
    assert continuation.previous_run_id == final.run_ids[0]
    assert "AGENTS.md" in (Path(final.worktree) / "automation" / "runs" / final.run_ids[-1] / "HANDOFF.md").read_text()

