from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest

from kalshi_stats.automation_runner import (
    ALLOWED_CONTAINER_ENVIRONMENT,
    RUN_FILENAMES,
    RunnerConfig,
    SecurityViolation,
    _create_git_bundle,
    build_codex_command,
    classify_runner_result,
    create_recovery_bootstrap,
    create_run_directory,
    execute_launch,
    prepare_launch,
    redact_diagnostic_text,
    select_container_environment,
    verify_task_worktree,
)
from kalshi_stats.automation_state import (
    ErrorClassification,
    RunRecord,
    TaskRecord,
    TaskStatus,
    save_task,
)


NOW = "2026-08-31T12:00:00Z"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(path), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _records(worktree: Path, branch: str = "automation/task-one"):
    task = TaskRecord.create(
        task_id="task-one",
        title="Bounded task",
        objective="Exercise the isolated runner.",
        branch=branch,
        worktree=str(worktree),
        now=NOW,
    )
    run = RunRecord(
        run_id="run-one",
        task_id=task.task_id,
        status=TaskStatus.RUNNING,
        session_thread_id=None,
        started_at=NOW,
        finished_at=None,
        branch=branch,
        worktree=str(worktree),
        files_changed=(),
        validation_results={},
        final_response_path="automation/runs/run-one/final.md",
        jsonl_log_path="automation/runs/run-one/events.jsonl",
        error_classification=None,
        next_action="Execute the bounded prompt.",
    )
    return task, run


@pytest.fixture
def isolated_git(tmp_path):
    primary = tmp_path / "stats"
    allowed = tmp_path / "stats-auto"
    worktree = allowed / "task-one"
    primary.mkdir()
    allowed.mkdir()
    subprocess.run(("git", "init", "-b", "main", str(primary)), check=True, capture_output=True)
    _git(primary, "config", "user.name", "Automation Test")
    _git(primary, "config", "user.email", "automation-test.invalid")
    (primary / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(primary, "add", "README.md")
    _git(primary, "commit", "-m", "fixture")
    _git(primary, "worktree", "add", "-b", "automation/task-one", str(worktree))

    (worktree / "automation" / "tasks").mkdir(parents=True)
    (worktree / "automation" / "runs").mkdir(parents=True)
    prompt = worktree / "task-prompt.md"
    prompt.write_text("Implement only the bounded fixture task.\n", encoding="utf-8")
    auth = tmp_path / "automation-codex-home"
    auth.mkdir()
    task, run = _records(worktree)
    save_task(worktree / "automation" / "tasks" / "task-one.json", task)
    return {
        "primary": primary,
        "allowed": allowed,
        "worktree": worktree,
        "prompt": prompt,
        "auth": auth,
        "task": task,
        "run": run,
    }


def _config(paths, **overrides):
    values = {
        "allowed_worktree_root": paths["allowed"],
        "primary_runtime_worktree": paths["primary"],
        "prompt_path": paths["prompt"],
        "run_directory": paths["worktree"] / "automation" / "runs" / "run-one",
        "auth_directory": paths["auth"],
        "dry_run": True,
    }
    values.update(overrides)
    return RunnerConfig(**values)


def test_valid_isolated_task_worktree_is_accepted(isolated_git):
    identity = verify_task_worktree(
        isolated_git["task"],
        isolated_git["run"],
        allowed_worktree_root=isolated_git["allowed"],
        primary_runtime_worktree=isolated_git["primary"],
    )
    assert identity.path == isolated_git["worktree"].resolve()
    assert identity.branch == "automation/task-one"
    assert identity.git_common_dir == Path(
        _git(isolated_git["primary"], "rev-parse", "--path-format=absolute", "--git-common-dir")
    )


def test_primary_runtime_worktree_is_rejected(isolated_git):
    task, run = _records(isolated_git["primary"])
    with pytest.raises(SecurityViolation, match="primary runtime"):
        verify_task_worktree(
            task,
            run,
            allowed_worktree_root=isolated_git["primary"].parent,
            primary_runtime_worktree=isolated_git["primary"],
        )


@pytest.mark.parametrize("branch", ["main", "automation-integration"])
def test_main_and_integration_are_rejected_as_task_branches(isolated_git, branch):
    with pytest.raises(ValueError, match="not task work branches|task branch"):
        _records(isolated_git["worktree"], branch)


def test_path_escaping_allowed_worktree_root_is_rejected(isolated_git, tmp_path):
    narrow_root = tmp_path / "not-the-worktree-root"
    narrow_root.mkdir()
    with pytest.raises(SecurityViolation, match="escapes"):
        verify_task_worktree(
            isolated_git["task"],
            isolated_git["run"],
            allowed_worktree_root=narrow_root,
            primary_runtime_worktree=isolated_git["primary"],
        )


def test_broad_allowed_worktree_root_is_rejected(isolated_git):
    with pytest.raises(SecurityViolation, match="too broad"):
        verify_task_worktree(
            isolated_git["task"],
            isolated_git["run"],
            allowed_worktree_root=Path("/"),
            primary_runtime_worktree=isolated_git["primary"],
        )


def test_environment_forwarding_is_explicit_and_credential_safe():
    source = {
        "LANG": "C.UTF-8",
        "HTTPS_PROXY": "http://proxy.invalid",
        "UNRELATED": "must-not-propagate",
        "KALSHI_PRIVATE_KEY": "must-not-propagate",
    }
    assert select_container_environment(source, ("LANG",)) == {"LANG": "C.UTF-8"}
    assert "UNRELATED" not in ALLOWED_CONTAINER_ENVIRONMENT
    with pytest.raises(SecurityViolation, match="credential-like"):
        select_container_environment(source, ("KALSHI_PRIVATE_KEY",))
    with pytest.raises(SecurityViolation, match="not allowlisted"):
        select_container_environment(source, ("UNRELATED",))


def test_run_directory_is_created_with_complete_contract(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    task, run = _records(worktree)
    run_dir = tmp_path / run.run_id
    create_run_directory(run_dir, task, run)

    assert {path.name for path in run_dir.iterdir()} == set(RUN_FILENAMES)
    assert json.loads((run_dir / "state.json").read_text(encoding="utf-8"))["run_id"] == run.run_id
    assert json.loads((run_dir / "validation.json").read_text(encoding="utf-8")) == {}
    assert task.task_id in (run_dir / "HANDOFF.md").read_text(encoding="utf-8")

    create_run_directory(run_dir, task, run)
    with pytest.raises(SecurityViolation, match="supplied RunRecord"):
        create_run_directory(run_dir, task, replace(run, next_action="Different state."))


def test_recovery_bootstrap_contains_every_required_source(tmp_path):
    worktree = tmp_path / "worktree"
    task_json = worktree / "automation" / "tasks" / "task-one.json"
    run_dir = worktree / "automation" / "runs" / "run-one"
    task, run = _records(worktree)
    text = create_recovery_bootstrap(task, run, task_json_path=task_json, run_directory=run_dir)

    for required in (
        "AGENTS.md",
        "README.md",
        "docs/RESEARCH_SYSTEM.md",
        "docs/CODEX_HANDOFF.md",
        "docs/AUTOMATION_CHECKLIST.md",
        "automation/tasks/task-one.json",
        "automation/runs/run-one/HANDOFF.md",
        "git status --short",
        "git diff",
        "git log",
        "automation/runs/run-one/validation.json",
    ):
        assert required in text


def test_docker_command_has_only_allowed_mounts_and_hardening(isolated_git):
    plan = prepare_launch(isolated_git["task"], isolated_git["run"], _config(isolated_git))
    command = list(plan.docker_command)
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]

    assert mounts == [
        f"type=bind,src={isolated_git['worktree'].resolve()},dst=/workspace",
        f"type=bind,src={isolated_git['auth'].resolve()},dst=/codex-home",
    ]
    joined = " ".join(command)
    assert "docker.sock" not in joined
    assert "--privileged" not in command
    assert "--interactive" in command
    assert "--network bridge" in joined
    assert "--cap-drop ALL" in joined
    assert "no-new-privileges" in joined
    assert "HTTPS_PROXY=" in command
    assert "https_proxy=" in command


def test_codex_command_uses_inspected_noninteractive_no_approval_syntax():
    command = build_codex_command(
        final_path=Path("/workspace/automation/runs/run-one/final.md"),
        model="gpt-5.4",
        reasoning_effort="high",
    )
    assert command[0] == "codex"
    assert "--json" in command
    assert command[command.index("--ask-for-approval") + 1] == "never"
    assert command[command.index("--sandbox") + 1] == "danger-full-access"
    assert command.index("--ask-for-approval") < command.index("exec")
    assert command.index("--sandbox") < command.index("exec")
    assert command[command.index("--cd") + 1] == "/workspace"
    assert "--output-last-message" in command
    assert 'model_reasoning_effort="high"' in command
    assert command[-1] == "-"


def test_dry_run_performs_no_docker_or_codex_execution(isolated_git, monkeypatch):
    plan = prepare_launch(isolated_git["task"], isolated_git["run"], _config(isolated_git))

    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run attempted process execution")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = execute_launch(plan)
    assert result["dry_run"] is True
    assert not plan.git_bundle_path.exists()


def test_git_bundle_is_atomically_replaceable(isolated_git):
    plan = prepare_launch(isolated_git["task"], isolated_git["run"], _config(isolated_git))
    _create_git_bundle(plan)
    first_size = plan.git_bundle_path.stat().st_size
    _create_git_bundle(plan)

    assert plan.git_bundle_path.stat().st_size == first_size
    _git(isolated_git["primary"], "bundle", "verify", str(plan.git_bundle_path))
    assert not plan.git_bundle_path.with_name(f".{plan.git_bundle_path.name}.tmp").exists()


@pytest.mark.parametrize(
    ("exit_code", "text", "timed_out", "expected"),
    [
        (0, "", False, ErrorClassification.SUCCESS),
        (1, "HTTP 429 rate limit", False, ErrorClassification.RATE_LIMITED),
        (1, "maximum context length", False, ErrorClassification.CONTEXT_EXHAUSTED),
        (1, "SyntaxError", False, ErrorClassification.CODE_FAILURE),
        (1, "pytest: 2 tests failed", False, ErrorClassification.TEST_FAILURE),
        (1, "cannot connect to Docker daemon", False, ErrorClassification.INFRASTRUCTURE_FAILURE),
        (1, "No prompt provided via stdin.", False, ErrorClassification.INFRASTRUCTURE_FAILURE),
        (1, "SECURITY_VIOLATION", False, ErrorClassification.SECURITY_VIOLATION),
        (1, "database disk image is malformed", False, ErrorClassification.DATABASE_INTEGRITY_FAILURE),
        (1, "unrecognized failure", False, ErrorClassification.UNKNOWN_FAILURE),
        (137, "", True, ErrorClassification.INFRASTRUCTURE_FAILURE),
    ],
)
def test_error_classification_hooks(exit_code, text, timed_out, expected):
    assert classify_runner_result(exit_code, text, timed_out=timed_out) is expected


def test_secret_value_never_appears_in_generated_diagnostic(isolated_git):
    secret_value = "credential-value-that-must-not-be-logged"
    plan = prepare_launch(
        isolated_git["task"],
        isolated_git["run"],
        _config(isolated_git, environment_names=("HTTPS_PROXY",)),
        source_environment={"HTTPS_PROXY": secret_value},
    )
    rendered = json.dumps(plan.diagnostic(), sort_keys=True)
    assert secret_value not in rendered
    assert "HTTPS_PROXY" in rendered


def test_auth_path_never_appears_in_ordinary_diagnostic(isolated_git):
    plan = prepare_launch(isolated_git["task"], isolated_git["run"], _config(isolated_git))
    rendered = json.dumps(plan.diagnostic(), sort_keys=True)
    assert str(isolated_git["auth"].resolve()) not in rendered
    assert "<dedicated-automation-auth>" in rendered


def test_credential_shaped_log_values_are_redacted():
    secret = "credential-value-that-must-not-be-logged"
    rendered = redact_diagnostic_text(
        f"auth_token={secret} Bearer abc.def.ghi sk-exampleSecret123",
    )
    assert secret not in rendered
    assert "abc.def.ghi" not in rendered
    assert "sk-exampleSecret123" not in rendered
    assert rendered.count("[REDACTED]") == 3


def test_auth_mount_must_be_dedicated_and_outside_worktree(isolated_git):
    in_worktree = isolated_git["worktree"] / "codex-home"
    in_worktree.mkdir()
    with pytest.raises(SecurityViolation, match="outside"):
        prepare_launch(
            isolated_git["task"],
            isolated_git["run"],
            _config(isolated_git, auth_directory=in_worktree),
        )


def test_user_credential_directories_cannot_be_auth_mounts(isolated_git, monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    forbidden = fake_home / ".ssh" / "nested"
    forbidden.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    with pytest.raises(SecurityViolation, match="authentication mounts"):
        prepare_launch(
            isolated_git["task"],
            isolated_git["run"],
            _config(isolated_git, auth_directory=forbidden),
        )


def test_docker_build_context_is_deny_by_default():
    root = Path(__file__).resolve().parents[1]
    rules = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert rules[0] == "**"
    assert "!src/**" in rules
    assert "!automation/container/**" in rules
    assert not any(".env" in rule or ".git" in rule for rule in rules[1:])

def test_explicit_usage_limit_is_not_misclassified_as_security_violation():
    from kalshi_stats.automation_runner import classify_runner_result
    from kalshi_stats.automation_state import ErrorClassification

    diagnostic = """
    Reviewer is checking the security boundary.
    You've hit your usage limit. Purchase more credits or try again at 9:22 PM.
    """

    assert (
        classify_runner_result(1, diagnostic)
        is ErrorClassification.RATE_LIMITED
    )

    assert (
        classify_runner_result(
            1,
            "Reviewer is checking the security boundary.",
        )
        is ErrorClassification.UNKNOWN_FAILURE
    )
