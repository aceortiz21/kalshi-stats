"""Fail-closed Git worktree lifecycle for Automation V1 tasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable, Sequence

from .automation_state import is_valid_task_branch


FORBIDDEN_TASK_BRANCHES = frozenset({"main", "automation-integration"})


class WorktreeLifecycleError(RuntimeError):
    """A worktree operation that cannot be completed without risking user work."""


@dataclass(frozen=True)
class TaskWorktree:
    path: Path
    branch: str
    base_branch: str
    base_sha: str
    git_common_dir: Path


def _git(repository: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), *arguments),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise WorktreeLifecycleError(f"Git command could not start: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git failure"
        raise WorktreeLifecycleError(f"Git operation failed: {detail}")
    return result


def _repository_root(repository: Path) -> Path:
    try:
        resolved = repository.resolve(strict=True)
    except OSError as exc:
        raise WorktreeLifecycleError(f"repository is unavailable: {exc}") from exc
    result = _git(resolved, "rev-parse", "--show-toplevel")
    top = Path(result.stdout.strip()).resolve(strict=True)
    if top != resolved:
        raise WorktreeLifecycleError("repository must be the root of a Git worktree")
    return resolved


def _allowed_root(allowed_worktree_root: Path, primary_runtime_worktree: Path) -> Path:
    if allowed_worktree_root.is_symlink():
        raise WorktreeLifecycleError("allowed worktree root must not be a symbolic link")
    try:
        root = allowed_worktree_root.resolve(strict=True)
        primary = primary_runtime_worktree.resolve(strict=True)
    except OSError as exc:
        raise WorktreeLifecycleError(f"worktree boundary is unavailable: {exc}") from exc
    if not root.is_dir():
        raise WorktreeLifecycleError("allowed worktree root must be a directory")
    if root in {Path("/"), Path("/home"), Path.home().resolve()}:
        raise WorktreeLifecycleError("allowed worktree root is too broad")
    if root == primary or root.is_relative_to(primary):
        raise WorktreeLifecycleError("allowed worktree root overlaps the primary runtime")
    return root


def _task_path(path: Path, allowed_root: Path, primary_runtime_worktree: Path) -> Path:
    if path.is_symlink():
        raise WorktreeLifecycleError("task worktree path must not be a symbolic link")
    candidate = path.resolve(strict=False)
    primary = primary_runtime_worktree.resolve(strict=True)
    if candidate.parent != allowed_root:
        raise WorktreeLifecycleError(
            "task worktree must be one direct child of the configured automation root"
        )
    if candidate == primary or candidate.is_relative_to(primary):
        raise WorktreeLifecycleError("primary runtime worktree is forbidden")
    return candidate


def _validate_branches(
    *,
    base_branch: str,
    task_branch: str,
    allowed_base_branches: Iterable[str],
) -> None:
    allowed = frozenset(allowed_base_branches)
    if base_branch not in allowed:
        raise WorktreeLifecycleError("source/base branch is not explicitly allowed")
    if task_branch in FORBIDDEN_TASK_BRANCHES or not is_valid_task_branch(task_branch):
        raise WorktreeLifecycleError("invalid autonomous task branch")
    if task_branch == base_branch:
        raise WorktreeLifecycleError("task branch must be distinct from its source branch")


def _registered_worktrees(repository: Path) -> list[tuple[Path, str | None]]:
    output = _git(repository, "worktree", "list", "--porcelain").stdout
    records: list[tuple[Path, str | None]] = []
    current_path: Path | None = None
    current_branch: str | None = None
    for line in (*output.splitlines(), ""):
        if not line:
            if current_path is not None:
                records.append((current_path.resolve(), current_branch))
            current_path = None
            current_branch = None
        elif line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/"):
            current_branch = line.removeprefix("branch refs/heads/")
    return records


def create_task_worktree(
    *,
    repository: Path,
    base_branch: str,
    task_branch: str,
    worktree_path: Path,
    allowed_worktree_root: Path,
    allowed_base_branches: Iterable[str],
    primary_runtime_worktree: Path,
) -> TaskWorktree:
    """Create and Git-verify one new isolated task branch/worktree."""

    repo = _repository_root(repository)
    root = _allowed_root(allowed_worktree_root, primary_runtime_worktree)
    target = _task_path(worktree_path, root, primary_runtime_worktree)
    _validate_branches(
        base_branch=base_branch,
        task_branch=task_branch,
        allowed_base_branches=allowed_base_branches,
    )
    if target.exists():
        raise WorktreeLifecycleError("task worktree path already exists")
    if any(path == target or branch == task_branch for path, branch in _registered_worktrees(repo)):
        raise WorktreeLifecycleError("conflicting registered worktree or branch")
    if _git(repo, "show-ref", "--verify", f"refs/heads/{base_branch}", check=False).returncode:
        raise WorktreeLifecycleError("allowed source/base branch does not exist locally")
    if not _git(repo, "show-ref", "--verify", f"refs/heads/{task_branch}", check=False).returncode:
        raise WorktreeLifecycleError("task branch already exists")
    if _git(repo, "check-ref-format", "--branch", task_branch, check=False).returncode:
        raise WorktreeLifecycleError("task branch fails Git ref validation")

    base_sha = _git(repo, "rev-parse", f"refs/heads/{base_branch}").stdout.strip()
    _git(repo, "worktree", "add", "-b", task_branch, str(target), base_branch)
    return inspect_task_worktree(
        repository=repo,
        base_branch=base_branch,
        task_branch=task_branch,
        worktree_path=target,
        allowed_worktree_root=root,
        allowed_base_branches=allowed_base_branches,
        primary_runtime_worktree=primary_runtime_worktree,
        expected_base_sha=base_sha,
    )


def inspect_task_worktree(
    *,
    repository: Path,
    base_branch: str,
    task_branch: str,
    worktree_path: Path,
    allowed_worktree_root: Path,
    allowed_base_branches: Iterable[str],
    primary_runtime_worktree: Path,
    expected_base_sha: str | None = None,
) -> TaskWorktree:
    """Inspect ownership using Git metadata, not path strings alone."""

    repo = _repository_root(repository)
    root = _allowed_root(allowed_worktree_root, primary_runtime_worktree)
    target = _task_path(worktree_path, root, primary_runtime_worktree)
    _validate_branches(
        base_branch=base_branch,
        task_branch=task_branch,
        allowed_base_branches=allowed_base_branches,
    )
    if not target.is_dir():
        raise WorktreeLifecycleError("task worktree does not exist")
    registered = dict(_registered_worktrees(repo))
    if registered.get(target) != task_branch:
        raise WorktreeLifecycleError("Git does not register the expected branch/worktree pair")
    top = Path(_git(target, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    current_branch = _git(target, "branch", "--show-current").stdout.strip()
    if top != target or current_branch != task_branch:
        raise WorktreeLifecycleError("worktree root or checked-out branch disagrees with ownership")
    repo_common = Path(
        _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    ).resolve()
    task_common = Path(
        _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    ).resolve()
    if repo_common != task_common:
        raise WorktreeLifecycleError("task worktree belongs to an unrelated Git repository")
    base_sha = _git(repo, "rev-parse", f"refs/heads/{base_branch}").stdout.strip()
    if expected_base_sha is not None and base_sha != expected_base_sha:
        raise WorktreeLifecycleError("source/base branch moved during worktree creation")
    return TaskWorktree(target, task_branch, base_branch, base_sha, task_common)


def _listed_paths(worktree: Path, *arguments: str) -> tuple[Path, ...]:
    result = subprocess.run(
        ("git", "-C", str(worktree), "ls-files", "-z", *arguments),
        check=True,
        capture_output=True,
    )
    return tuple(Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value)


def _is_allowed_disposable(path: Path, allowed: Sequence[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in allowed)


def cleanup_task_worktree(
    *,
    repository: Path,
    base_branch: str,
    task_branch: str,
    worktree_path: Path,
    allowed_worktree_root: Path,
    allowed_base_branches: Iterable[str],
    primary_runtime_worktree: Path,
    disposable_paths: Sequence[Path] = (),
) -> None:
    """Remove a clean owned task worktree without force-deleting unknown work."""

    owned = inspect_task_worktree(
        repository=repository,
        base_branch=base_branch,
        task_branch=task_branch,
        worktree_path=worktree_path,
        allowed_worktree_root=allowed_worktree_root,
        allowed_base_branches=allowed_base_branches,
        primary_runtime_worktree=primary_runtime_worktree,
    )
    status = _git(owned.path, "status", "--porcelain=v1", "--untracked-files=all").stdout
    if status.strip():
        raise WorktreeLifecycleError("worktree has unexpected uncommitted changes")

    allowed = tuple(Path(path) for path in disposable_paths)
    for path in allowed:
        if path.is_absolute() or path == Path(".") or ".." in path.parts:
            raise WorktreeLifecycleError("disposable cleanup paths must be safe relative paths")
    untracked = _listed_paths(owned.path, "--others", "--exclude-standard")
    ignored = _listed_paths(owned.path, "--others", "--ignored", "--exclude-standard")
    unexpected = [path for path in (*untracked, *ignored) if not _is_allowed_disposable(path, allowed)]
    if unexpected:
        raise WorktreeLifecycleError(
            f"worktree contains unexpected disposable content: {unexpected[0]}"
        )

    repo = _repository_root(repository)
    branch_sha = _git(repo, "rev-parse", f"refs/heads/{task_branch}").stdout.strip()
    base_sha = _git(repo, "rev-parse", f"refs/heads/{base_branch}").stdout.strip()
    if branch_sha != base_sha:
        raise WorktreeLifecycleError("task branch contains commits and will not be deleted")
    _git(repo, "worktree", "remove", str(owned.path))
    _git(repo, "branch", "-d", task_branch)
