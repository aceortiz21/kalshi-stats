from pathlib import Path

from kalshi_stats.automation_quota import run_with_quota_wait
from kalshi_stats.automation_state import (
    ErrorClassification,
    RunRecord,
    TaskRecord,
    TaskStatus,
    load_run,
    load_task,
    save_run,
    save_task,
)

NOW = "2026-08-31T12:00:00Z"


def _fixture(tmp_path: Path):
    task_path, run_path = tmp_path / "task.json", tmp_path / "run.json"
    task = TaskRecord.create(
        task_id="quota-task", title="Quota task", objective="Test quota waiting.",
        branch="automation/quota-task", worktree=str(tmp_path), now=NOW,
    )
    task = TaskRecord(**{**task.to_dict(), "status": "RUNNING", "attempt_count": 1})
    run = RunRecord(
        run_id="quota-run", task_id=task.task_id, status=TaskStatus.RUNNING,
        session_thread_id=None, started_at=NOW, finished_at=None,
        branch=task.branch, worktree=task.worktree, files_changed=(),
        validation_results={"evidence": "preserved"}, final_response_path="final.md",
        jsonl_log_path="events.jsonl", error_classification=None, next_action="Run.",
    )
    save_task(task_path, task)
    save_run(run_path, run)
    return task_path, run_path


def test_rate_limit_resume_preserves_attempt_and_uses_injected_sleep(tmp_path):
    task_path, run_path = _fixture(tmp_path)
    outcomes = iter([
        {"error_classification": "RATE_LIMITED"},
        {"error_classification": "SUCCESS"},
    ])
    sleeps, now = [], [100.0]

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    calls = []
    result = run_with_quota_wait(
        task_path, run_path,
        lambda task, run: (calls.append((task.status, run.status, task.attempt_count)) or next(outcomes)),
        backoff_seconds=(5, 15, 30), max_wait_seconds=100,
        sleep=sleep, clock=lambda: now[0],
    )
    assert result["error_classification"] == "SUCCESS"
    assert sleeps == [5]
    assert calls == [(TaskStatus.RUNNING, TaskStatus.RUNNING, 1)] * 2
    assert load_task(task_path).attempt_count == 1
    assert load_run(run_path).validation_results["evidence"] == "preserved"


def test_backoff_sequence_repeats_and_non_rate_limit_does_not_retry(tmp_path):
    task_path, run_path = _fixture(tmp_path)
    outcomes = iter([
        {"error_classification": "RATE_LIMITED"},
        {"error_classification": "RATE_LIMITED"},
        {"error_classification": "CODE_FAILURE"},
    ])
    sleeps, now = [], [0.0]
    result = run_with_quota_wait(
        task_path, run_path, lambda task, run: next(outcomes),
        backoff_seconds=(5, 15), max_wait_seconds=100,
        sleep=lambda seconds: (sleeps.append(seconds), now.__setitem__(0, now[0] + seconds)),
        clock=lambda: now[0],
    )
    assert result["error_classification"] == "CODE_FAILURE"
    assert sleeps == [5, 15]


def test_persisted_waiting_state_fails_closed_at_horizon(tmp_path):
    task_path, run_path = _fixture(tmp_path)
    calls, now = [], [0.0]
    result = run_with_quota_wait(
        task_path, run_path,
        lambda task, run: (calls.append(1) or {"error_classification": "RATE_LIMITED"}),
        backoff_seconds=(5,), max_wait_seconds=5,
        sleep=lambda seconds: (now.__setitem__(0, now[0] + seconds)), clock=lambda: now[0],
    )
    assert result["status"] == "FAILED"
    assert calls == [1]
    assert load_task(task_path).status is TaskStatus.FAILED
    assert load_run(run_path).status is TaskStatus.FAILED


def test_waiting_state_survives_wrapper_reconstruction(tmp_path):
    task_path, run_path = _fixture(tmp_path)
    first_sleep = []

    def stop_after_persist(seconds):
        first_sleep.append(seconds)
        raise RuntimeError("simulated supervisor stop")

    try:
        run_with_quota_wait(
            task_path, run_path,
            lambda task, run: {"error_classification": "RATE_LIMITED"},
            backoff_seconds=(5,), max_wait_seconds=100,
            sleep=stop_after_persist, clock=lambda: 0.0,
        )
    except RuntimeError as exc:
        assert str(exc) == "simulated supervisor stop"
    assert first_sleep == [5]
    assert load_task(task_path).status is TaskStatus.WAITING_FOR_QUOTA
    assert load_run(run_path).validation_results["quota_wait"]["backoff_index"] == 1


def test_diagnostics_are_not_persisted_by_quota_wrapper(tmp_path):
    task_path, run_path = _fixture(tmp_path)
    secret = "sk-super-secret-value"
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    run_with_quota_wait(
        task_path, run_path,
        lambda task, run: {"error_classification": ErrorClassification.RATE_LIMITED.value, "diagnostic": secret},
        backoff_seconds=(5,), max_wait_seconds=1,
        sleep=sleep, clock=lambda: now[0],
    )
    assert secret not in run_path.read_text(encoding="utf-8")
