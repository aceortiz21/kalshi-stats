from dataclasses import fields
import json
from pathlib import Path
import re

import pytest

from kalshi_stats.automation_state import (
    ALLOWED_TASK_TRANSITIONS,
    REQUIRED_CONTEXT_RECOVERY_INPUTS,
    ErrorClassification,
    RunRecord,
    TaskRecord,
    TaskStatus,
    load_run,
    load_task,
    is_valid_autonomous_integration_target,
    is_valid_task_branch,
    save_run,
    save_task,
    transition_task,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-31T12:00:00Z"
LATER = "2026-08-31T12:01:00Z"


def _task(**overrides):
    values = {
        "task_id": "phase-a-001",
        "title": "Build durable state",
        "objective": "Persist recoverable task and run records.",
        "branch": "automation/task-phase-a-001",
        "worktree": "/worktrees/phase-a-001",
        "prerequisites": ("bootstrap",),
        "max_attempts": 3,
        "next_action": "Start the isolated task.",
        "now": NOW,
    }
    values.update(overrides)
    return TaskRecord.create(**values)


def _run(**overrides):
    values = {
        "run_id": "run-001",
        "task_id": "phase-a-001",
        "status": TaskStatus.VALIDATING,
        "session_thread_id": None,
        "started_at": NOW,
        "finished_at": None,
        "branch": "automation/task-phase-a-001",
        "worktree": "/worktrees/phase-a-001",
        "files_changed": ("src/kalshi_stats/automation_state.py",),
        "validation_results": {
            "compileall": {"status": "PASSED", "exit_code": 0}
        },
        "final_response_path": "automation/runs/run-001/final.md",
        "jsonl_log_path": "automation/runs/run-001/events.jsonl",
        "error_classification": None,
        "next_action": "Run the full test suite.",
    }
    values.update(overrides)
    return RunRecord(**values)


def test_valid_task_creation_has_complete_queued_state():
    task = _task()

    assert task.status is TaskStatus.QUEUED
    assert task.attempt_count == 0
    assert task.created_at == task.updated_at == NOW
    assert set(task.to_dict()) == {field.name for field in fields(TaskRecord)}


def test_invalid_status_is_rejected():
    payload = _task().to_dict()
    payload["status"] = "NOT_A_STATUS"

    with pytest.raises(ValueError, match="invalid task status"):
        TaskRecord.from_dict(payload)


def test_allowed_transitions_are_explicit_and_attempts_are_bounded():
    assert ALLOWED_TASK_TRANSITIONS[TaskStatus.QUEUED] == frozenset(
        {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.ARCHIVED}
    )
    running = transition_task(
        _task(),
        TaskStatus.RUNNING,
        next_action="Perform the scoped implementation.",
        updated_at=LATER,
    )
    assert running.status is TaskStatus.RUNNING
    assert running.attempt_count == 1
    waiting = transition_task(
        running,
        TaskStatus.WAITING_FOR_QUOTA,
        next_action="Wait for quota without consuming an attempt.",
        updated_at="2026-08-31T12:02:00Z",
    )
    resumed = transition_task(
        waiting,
        TaskStatus.RUNNING,
        next_action="Resume the same attempt.",
        updated_at="2026-08-31T12:03:00Z",
    )
    assert resumed.attempt_count == 1

    exhausted = _task(max_attempts=1)
    exhausted = transition_task(
        exhausted,
        TaskStatus.RUNNING,
        next_action="Run once.",
        updated_at=LATER,
    )
    exhausted = transition_task(
        exhausted,
        TaskStatus.FAILED,
        next_action="Record the failure.",
        updated_at="2026-08-31T12:02:00Z",
    )
    exhausted = transition_task(
        exhausted,
        TaskStatus.QUEUED,
        next_action="Await retry eligibility.",
        updated_at="2026-08-31T12:03:00Z",
    )
    with pytest.raises(ValueError, match="exhausted max_attempts"):
        transition_task(
            exhausted,
            TaskStatus.RUNNING,
            next_action="Must not run.",
            updated_at="2026-08-31T12:04:00Z",
        )


def test_disallowed_transition_is_rejected():
    with pytest.raises(ValueError, match="QUEUED -> PASSED"):
        transition_task(
            _task(), TaskStatus.PASSED, next_action="Invalid direct pass."
        )


def test_task_atomic_persistence_replaces_and_round_trips(tmp_path):
    path = tmp_path / "tasks" / "phase-a-001.json"
    original = _task()
    save_task(path, original)

    running = transition_task(
        original,
        TaskStatus.RUNNING,
        next_action="Continue from durable state.",
        updated_at=LATER,
    )
    save_task(path, running)

    assert load_task(path) == running
    assert json.loads(path.read_text(encoding="utf-8")) == running.to_dict()
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_run_state_persistence_contains_required_fields(tmp_path):
    path = tmp_path / "runs" / "run-001" / "state.json"
    run = _run(error_classification=ErrorClassification.TEST_FAILURE)

    save_run(path, run)

    assert load_run(path) == run
    assert set(run.to_dict()) == {field.name for field in fields(RunRecord)}
    assert run.to_dict()["error_classification"] == "TEST_FAILURE"


def test_context_recovery_contract_lists_every_required_input():
    assert REQUIRED_CONTEXT_RECOVERY_INPUTS == (
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
    contract = (ROOT / "automation" / "README.md").read_text(encoding="utf-8")
    for required in REQUIRED_CONTEXT_RECOVERY_INPUTS[:7]:
        assert required in contract
    for filename in (
        "HANDOFF.md",
        "state.json",
        "events.jsonl",
        "final.md",
        "validation.json",
        "errors.log",
    ):
        assert filename in contract


def test_error_classification_matches_machine_readable_schema():
    schema = json.loads(
        (
            ROOT
            / "automation"
            / "schemas"
            / "error-classification.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema["enum"] == [classification.value for classification in ErrorClassification]


@pytest.mark.parametrize(
    "branch",
    [
        "automation-integration",
        "main",
        "feature/unscoped",
        "automation/task-",
        "automation/nested/task",
    ],
)
def test_invalid_task_branches_are_rejected(branch):
    assert not is_valid_task_branch(branch)
    with pytest.raises(ValueError, match="not task work branches|task branch"):
        _task(branch=branch)
    with pytest.raises(ValueError, match="not task work branches|task branch"):
        _run(branch=branch)


@pytest.mark.parametrize(
    "branch",
    [
        "auto/example",
        "automation/example",
        "automation/bootstrap-v1",
    ],
)
def test_valid_task_branches_match_git_policy(branch):
    assert is_valid_task_branch(branch)
    assert _task(branch=branch).branch == branch
    assert _run(branch=branch).branch == branch


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("automation-integration", True),
        ("main", False),
        ("auto/example", False),
        ("automation/example", False),
        ("automation/nested/task", False),
    ],
)
def test_autonomous_integration_target_is_distinct(branch, expected):
    assert is_valid_autonomous_integration_target(branch) is expected


@pytest.mark.parametrize("schema_name", ["task.schema.json", "run.schema.json"])
def test_record_schemas_allow_only_task_work_branches(schema_name):
    schema = json.loads(
        (ROOT / "automation" / "schemas" / schema_name).read_text(encoding="utf-8")
    )
    branch_rules = schema["properties"]["branch"]["anyOf"]

    assert {rule["pattern"] for rule in branch_rules} == {
        "^auto/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$",
        "^automation/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$",
    }
    patterns = [re.compile(rule["pattern"]) for rule in branch_rules]
    assert any(pattern.fullmatch("automation/bootstrap-v1") for pattern in patterns)
    assert not any(pattern.fullmatch("automation-integration") for pattern in patterns)
    assert not any(pattern.fullmatch("main") for pattern in patterns)


def test_policy_manifest_separates_task_branches_from_integration_destination():
    manifest = json.loads(
        (ROOT / "automation" / "policies" / "policy-manifest.json").read_text(
            encoding="utf-8"
        )
    )["git"]

    assert manifest["autonomous_integration_targets"] == [
        "automation-integration"
    ]
    assert manifest["task_records_may_use_integration_targets"] is False
    assert manifest["run_records_may_use_integration_targets"] is False
    assert manifest["human_only_branches"] == ["main"]
