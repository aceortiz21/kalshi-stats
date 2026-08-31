from dataclasses import replace
import json
from pathlib import Path

import pytest

from kalshi_stats.automation_canary import (
    CANARY_EXPECTED_LINE,
    CANARY_TEMPLATE,
    CanaryDefinition,
    PreparedCanary,
    render_canary_prompt,
    validate_canary_success,
)
from kalshi_stats.automation_runner import RUN_FILENAMES
from kalshi_stats.automation_state import RunRecord, TaskRecord, TaskStatus


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-31T12:00:00Z"


def _prepared(tmp_path: Path) -> PreparedCanary:
    worktree = tmp_path / "task"
    definition = CanaryDefinition(
        task_id="canary-one",
        run_id="canary-run-one",
        branch="automation/canary-one",
        base_branch="automation/base",
        worktree=worktree,
    )
    task = TaskRecord.create(
        task_id=definition.task_id,
        title="Canary",
        objective="Write the exact canary output.",
        branch=definition.branch,
        worktree=str(worktree),
        now=NOW,
    )
    run = RunRecord(
        run_id=definition.run_id,
        task_id=definition.task_id,
        status=TaskStatus.RUNNING,
        session_thread_id=None,
        started_at=NOW,
        finished_at=None,
        branch=definition.branch,
        worktree=str(worktree),
        files_changed=(),
        validation_results={},
        final_response_path=f"automation/runs/{definition.run_id}/final.md",
        jsonl_log_path=f"automation/runs/{definition.run_id}/events.jsonl",
        error_classification=None,
        next_action="Run.",
    )
    run_dir = worktree / "automation" / "runs" / definition.run_id
    run_dir.mkdir(parents=True)
    for name in RUN_FILENAMES:
        (run_dir / name).write_text("", encoding="utf-8")
    target = worktree / definition.target
    target.write_text(f"{CANARY_EXPECTED_LINE}\n", encoding="utf-8")
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "thread.started", "thread_id": "thread-123"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "final.md").write_text("Canary complete.\n", encoding="utf-8")
    state = replace(
        run,
        status=TaskStatus.VALIDATING,
        session_thread_id="thread-123",
        validation_results={
            "runner": {
                "exit_code": 0,
                "error_classification": "SUCCESS",
                "command_metadata": {
                    "codex_command": ["codex", "--ask-for-approval", "never", "exec"],
                    "mounts": [
                        {"source": str(worktree), "target": "/workspace"},
                        {"source": "<dedicated-automation-auth>", "target": "/codex-home"},
                    ],
                },
            }
        },
    )
    (run_dir / "state.json").write_text(json.dumps(state.to_dict()), encoding="utf-8")
    return PreparedCanary(definition, task, run, tmp_path / "prompt.md", run_dir, "abc123")


def test_canary_prompt_has_all_restrictions_and_exact_target():
    definition = CanaryDefinition(
        task_id="canary-one",
        run_id="canary-run-one",
        branch="automation/canary-one",
        base_branch="automation/base",
        worktree=Path("/tmp/task"),
    )
    template = (ROOT / CANARY_TEMPLATE).read_text(encoding="utf-8")
    prompt = render_canary_prompt(template, definition)
    assert definition.target.as_posix() in prompt
    assert CANARY_EXPECTED_LINE in prompt
    for restriction in (
        "Do not inspect credentials",
        "Do not access `main`",
        "Do not access `/home/aceortiz/stats` or `~/stats`",
        "Do not make network requests unless",
        "Do not make dependency",
        "Do not commit",
        "Stop immediately",
    ):
        assert restriction in prompt


def test_canary_success_validation(tmp_path):
    result = validate_canary_success(_prepared(tmp_path))
    assert result["status"] == "PASSED"
    assert result["human_command_approvals"] == 0
    assert result["exit_code"] == 0
    assert result["session_thread_id"] == "thread-123"


def test_canary_fails_if_expected_output_missing(tmp_path):
    prepared = _prepared(tmp_path)
    (prepared.definition.worktree / prepared.definition.target).unlink()
    with pytest.raises(RuntimeError, match="expected output"):
        validate_canary_success(prepared)


@pytest.mark.parametrize("filename", ["events.jsonl", "final.md"])
def test_canary_fails_if_events_or_final_evidence_absent(tmp_path, filename):
    prepared = _prepared(tmp_path)
    (prepared.run_directory / filename).write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="absent or empty"):
        validate_canary_success(prepared)


def test_zero_approval_success_criterion(tmp_path):
    prepared = _prepared(tmp_path)
    state_path = prepared.run_directory / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    command = state["validation_results"]["runner"]["command_metadata"]["codex_command"]
    command[command.index("never")] = "on-request"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RuntimeError, match="zero-approval"):
        validate_canary_success(prepared)


def test_approval_request_event_fails_canary(tmp_path):
    prepared = _prepared(tmp_path)
    (prepared.run_directory / "events.jsonl").write_text(
        json.dumps({"type": "command.approval_requested"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="zero-approval"):
        validate_canary_success(prepared)
