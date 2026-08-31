from pathlib import Path
import os
import subprocess

from kalshi_stats.automation_state import TaskRecord
from kalshi_stats.automation_validation import changed_files, worktree_fingerprint


def test_changed_files_and_fingerprint_do_not_access_live_database(tmp_path):
    root = tmp_path / "task"
    root.mkdir()
    subprocess.run(("git", "init", "-b", "automation/base", str(root)), check=True, capture_output=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "config", "user.name", "test"), check=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "config", "user.email", "test@invalid"), check=True, env=GIT_ENV)
    (root / "safe.txt").write_text("safe\n")
    subprocess.run(("git", "-C", str(root), "add", "."), check=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "commit", "-m", "base"), check=True, capture_output=True, env=GIT_ENV)
    before = worktree_fingerprint(root)
    (root / "new.txt").write_text("new\n")
    assert changed_files(root, "automation/base") == ("new.txt",)
    assert worktree_fingerprint(root) != before
    assert not (root / "data").exists()


def test_exact_base_sha_survives_missing_base_branch_ref(tmp_path):
    root = tmp_path / "task"
    root.mkdir()
    subprocess.run(("git", "init", "-b", "automation/base", str(root)), check=True, capture_output=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "config", "user.name", "test"), check=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "config", "user.email", "test@invalid"), check=True, env=GIT_ENV)
    (root / "base.txt").write_text("base\n")
    subprocess.run(("git", "-C", str(root), "add", "."), check=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "commit", "-m", "base"), check=True, capture_output=True, env=GIT_ENV)
    base_sha = subprocess.run(("git", "-C", str(root), "rev-parse", "HEAD"), check=True, capture_output=True, text=True, env=GIT_ENV).stdout.strip()
    subprocess.run(("git", "-C", str(root), "checkout", "-b", "automation/task"), check=True, capture_output=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "branch", "-D", "automation/base"), check=True, capture_output=True, env=GIT_ENV)
    (root / "task.txt").write_text("task\n")
    assert changed_files(root, base_sha) == ("task.txt",)
GIT_ENV = {key: value for key, value in os.environ.items() if key not in {"GIT_DIR", "GIT_WORK_TREE"}}

def test_command_gate_imports_task_worktree_src_before_host_pythonpath(
    tmp_path, monkeypatch
):
    import sys
    from kalshi_stats.automation_validation import _command_gate

    root = tmp_path / "task"
    src = root / "src"
    src.mkdir(parents=True)

    (src / "worktree_only_module.py").write_text(
        "VALUE = 'task-worktree'\n",
        encoding="utf-8",
    )

    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(external))

    result = _command_gate(
        root,
        "task_worktree_import",
        (
            sys.executable,
            "-c",
            "import worktree_only_module; "
            "assert worktree_only_module.VALUE == 'task-worktree'",
        ),
    )

    assert result.passed, result.detail
