import pytest


@pytest.fixture(autouse=True)
def _isolate_git_environment(monkeypatch):
    """The automation container pins its own worktree through these variables."""

    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
