from pathlib import Path
import subprocess

import pytest

from kalshi_stats.automation_state import RunRecord, TaskRecord, TaskStatus, load_run, load_task, save_run, save_task
from kalshi_stats.automation_worktrees import (
    WorktreeLifecycleError,
    cleanup_task_worktree,
    create_task_worktree,
    inspect_task_worktree,
)


NOW = "2026-08-31T12:00:00Z"


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(path), *args),
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repository(tmp_path):
    primary = tmp_path / "stats"
    allowed = tmp_path / "stats-auto" / "tasks"
    primary.mkdir()
    allowed.mkdir(parents=True)
    _git(primary, "init", "-b", "automation/base")
    _git(primary, "config", "user.name", "Automation Test")
    _git(primary, "config", "user.email", "automation-test.invalid")
    (primary / "README.md").write_text("fixture\n", encoding="utf-8")
    (primary / ".gitignore").write_text("generated/\n", encoding="utf-8")
    _git(primary, "add", "README.md", ".gitignore")
    _git(primary, "commit", "-m", "fixture")
    return primary, allowed


def _create(repository, *, branch="automation/task-one", name="task-one"):
    primary, allowed = repository
    return create_task_worktree(
        repository=primary,
        base_branch="automation/base",
        task_branch=branch,
        worktree_path=allowed / name,
        allowed_worktree_root=allowed,
        allowed_base_branches=("automation/base",),
        primary_runtime_worktree=primary,
    )


def _cleanup(repository, owned, *, disposable_paths=()):
    primary, allowed = repository
    cleanup_task_worktree(
        repository=primary,
        base_branch="automation/base",
        task_branch=owned.branch,
        worktree_path=owned.path,
        allowed_worktree_root=allowed,
        allowed_base_branches=("automation/base",),
        primary_runtime_worktree=primary,
        disposable_paths=disposable_paths,
    )


def test_valid_worktree_creation_and_git_inspection(repository):
    owned = _create(repository)
    inspected = inspect_task_worktree(
        repository=repository[0],
        base_branch="automation/base",
        task_branch=owned.branch,
        worktree_path=owned.path,
        allowed_worktree_root=repository[1],
        allowed_base_branches=("automation/base",),
        primary_runtime_worktree=repository[0],
    )
    assert inspected == owned
    assert inspected.path == repository[1] / "task-one"


@pytest.mark.parametrize("branch", ["feature/bad", "automation/nested/task", "task"])
def test_invalid_branch_rejection(repository, branch):
    with pytest.raises(WorktreeLifecycleError, match="invalid autonomous"):
        _create(repository, branch=branch)


@pytest.mark.parametrize("branch", ["main", "automation-integration"])
def test_main_and_automation_integration_task_rejection(repository, branch):
    with pytest.raises(WorktreeLifecycleError, match="invalid autonomous"):
        _create(repository, branch=branch)


def test_unallowed_base_branch_rejection(repository):
    primary, allowed = repository
    with pytest.raises(WorktreeLifecycleError, match="not explicitly allowed"):
        create_task_worktree(
            repository=primary,
            base_branch="automation/base",
            task_branch="automation/task-one",
            worktree_path=allowed / "task-one",
            allowed_worktree_root=allowed,
            allowed_base_branches=("automation/other",),
            primary_runtime_worktree=primary,
        )


def test_path_traversal_rejection(repository):
    primary, allowed = repository
    with pytest.raises(WorktreeLifecycleError, match="direct child"):
        create_task_worktree(
            repository=primary,
            base_branch="automation/base",
            task_branch="automation/task-one",
            worktree_path=allowed / ".." / "escape",
            allowed_worktree_root=allowed,
            allowed_base_branches=("automation/base",),
            primary_runtime_worktree=primary,
        )


def test_symlink_escape_rejection(repository, tmp_path):
    primary, allowed = repository
    outside = tmp_path / "outside"
    outside.mkdir()
    link = allowed / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorktreeLifecycleError, match="symbolic link|direct child"):
        create_task_worktree(
            repository=primary,
            base_branch="automation/base",
            task_branch="automation/task-one",
            worktree_path=link,
            allowed_worktree_root=allowed,
            allowed_base_branches=("automation/base",),
            primary_runtime_worktree=primary,
        )


def test_conflicting_worktree_rejection(repository):
    _create(repository)
    with pytest.raises(WorktreeLifecycleError, match="exists|conflicting"):
        _create(repository)


def test_dirty_worktree_safe_cleanup_refusal(repository):
    owned = _create(repository)
    (owned.path / "README.md").write_text("user work\n", encoding="utf-8")
    with pytest.raises(WorktreeLifecycleError, match="uncommitted changes"):
        _cleanup(repository, owned)
    assert owned.path.exists()
    assert _git(repository[0], "show-ref", "--verify", f"refs/heads/{owned.branch}").returncode == 0


def test_disposable_clean_worktree_cleanup(repository):
    owned = _create(repository)
    generated = owned.path / "generated" / "canary" / "result.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("ok\n", encoding="utf-8")
    _cleanup(repository, owned, disposable_paths=(Path("generated/canary"),))
    assert not owned.path.exists()
    assert _git(repository[0], "show-ref", "--verify", f"refs/heads/{owned.branch}", check=False).returncode != 0


def test_unknown_ignored_content_blocks_cleanup(repository):
    owned = _create(repository)
    generated = owned.path / "generated" / "unknown.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("unknown\n", encoding="utf-8")
    with pytest.raises(WorktreeLifecycleError, match="unexpected disposable content"):
        _cleanup(repository, owned, disposable_paths=(Path("generated/canary"),))


def test_durable_task_and_run_records_own_branch_and_worktree(repository):
    owned = _create(repository)
    task = TaskRecord.create(
        task_id="task-one",
        title="Canary",
        objective="Exercise ownership.",
        branch=owned.branch,
        worktree=str(owned.path),
        now=NOW,
    )
    run = RunRecord(
        run_id="run-one",
        task_id=task.task_id,
        status=TaskStatus.RUNNING,
        session_thread_id=None,
        started_at=NOW,
        finished_at=None,
        branch=owned.branch,
        worktree=str(owned.path),
        files_changed=(),
        validation_results={},
        final_response_path="automation/runs/run-one/final.md",
        jsonl_log_path="automation/runs/run-one/events.jsonl",
        error_classification=None,
        next_action="Run.",
    )
    save_task(owned.path / "task.json", task)
    save_run(owned.path / "run.json", run)
    assert load_task(owned.path / "task.json").worktree == str(owned.path)
    assert load_run(owned.path / "run.json").branch == owned.branch
