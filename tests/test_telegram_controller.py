import json
from pathlib import Path

import pytest

from kalshi_stats.automation_state import TaskRecord, TaskStatus, save_task
from kalshi_stats.telegram_controller import (
    AutomationController, ControllerPaths, OwnedProcess, TelegramAPI,
    TelegramCredentials, load_credentials,
)
from kalshi_stats import telegram_controller_cli


NOW = "2026-08-31T12:00:00Z"


class FakeAPI:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, text):
        if self.fail:
            raise RuntimeError("Telegram API request failed")
        self.sent.append(text)

    def updates(self, offset):
        return []


def task(repo, task_id="one", status=TaskStatus.QUEUED, **changes):
    record = TaskRecord.create(
        task_id=task_id, title=f"Title {task_id}", objective="bounded",
        branch=f"automation/{task_id}", worktree=str(repo / "tasks" / task_id),
        now=NOW,
    )
    values = {**record.to_dict(), "status": status.value, **changes}
    record = TaskRecord.from_dict(values)
    path = repo / "automation" / "tasks" / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    save_task(path, record)
    return record


@pytest.fixture
def controller(tmp_path):
    repo = tmp_path / "repo"
    (repo / "automation" / "tasks").mkdir(parents=True)
    api = FakeAPI()
    value = AutomationController(
        repository=repo, paths=ControllerPaths(tmp_path / "state"),
        credentials=TelegramCredentials("unit-test-placeholder", "123"),
        worker_command=("python", "-m", "kalshi_stats.automation_cli", "run", "--continuous"),
        api=api, docker_check=lambda: True, quota_notify_after=10,
    )
    return value, repo, api


def test_only_configured_chat_is_authorized(controller, monkeypatch):
    value, _, _ = controller
    called = []
    monkeypatch.setattr(value, "start_worker", lambda: called.append(True) or "started")
    assert value.handle(999, "/worker-start") is None
    assert called == []
    assert value.handle("123", "/worker-start") == "started"


def test_status_health_queue_task_and_worker_commands(controller, monkeypatch):
    value, repo, _ = controller
    task(repo, "queued")
    task(repo, "blocked", TaskStatus.BLOCKED, blocked_reason="human check")
    monkeypatch.setattr(value.worker, "running", lambda: False)
    assert "QUEUED=1" in value.handle(123, "/status")
    health = value.handle(123, "/health")
    assert "Telegram controller: RUNNING" in health
    assert "Docker available: YES" in health
    assert "Queue accessible: YES" in health
    assert "Human intervention required: YES" in health
    assert "queued: QUEUED" in value.handle(123, "/queue")
    assert "Status: BLOCKED" in value.handle(123, "/task blocked")
    assert "Worker: STOPPED" == value.handle(123, "/worker")


def test_health_requires_intervention_when_docker_is_unavailable(controller):
    value, _, _ = controller
    value.docker_check = lambda: False
    assert "Human intervention required: YES" in value.health_text()


def test_worker_start_does_not_duplicate(controller, monkeypatch):
    value, _, _ = controller
    monkeypatch.setattr(value.worker, "running", lambda: True)
    assert value.handle(123, "/worker-start") == "Worker: already running"


def test_worker_start_refuses_invalid_queue(controller, monkeypatch):
    value, repo, _ = controller
    (repo / "automation" / "tasks" / "corrupt.json").write_text("{not json")
    monkeypatch.setattr(value.worker, "start", lambda *args, **kwargs: pytest.fail("must not start worker"))
    assert "Worker start refused: invalid task record" in value.start_worker()


def test_worker_start_refuses_blocked_state(controller, monkeypatch):
    value, repo, _ = controller
    task(repo, status=TaskStatus.BLOCKED, blocked_reason="policy")
    monkeypatch.setattr(value.worker, "start", lambda *args, **kwargs: pytest.fail("must not start worker"))
    assert "Worker start refused: BLOCKED" in value.start_worker()


@pytest.mark.parametrize("status", [TaskStatus.RUNNING, TaskStatus.VALIDATING, TaskStatus.REVIEWING])
def test_worker_start_refuses_ambiguous_active_ownership(controller, monkeypatch, status):
    value, repo, _ = controller
    task(repo, status=status)
    monkeypatch.setattr(value.worker, "start", lambda *args, **kwargs: pytest.fail("must not start worker"))
    assert "Worker start refused: ambiguous ownership" in value.start_worker()


def test_worker_start_refuses_unavailable_docker(controller, monkeypatch):
    value, _, _ = controller
    value.docker_check = lambda: False
    monkeypatch.setattr(value.worker, "start", lambda *args, **kwargs: pytest.fail("must not start worker"))
    assert value.start_worker() == "Worker start refused: Docker is unavailable."


def test_worker_stop_signals_only_verified_owned_pid(tmp_path, monkeypatch):
    path = tmp_path / "worker.json"
    path.write_text(json.dumps({"role": "marker", "pid": 4321, "start_ticks": "9"}))
    owned = OwnedProcess(path, "marker")
    monkeypatch.setattr("kalshi_stats.telegram_controller._process_start_ticks", lambda pid: "9")
    monkeypatch.setattr("kalshi_stats.telegram_controller._process_cmdline", lambda pid: ("python", "marker"))
    killed = []
    monkeypatch.setattr("kalshi_stats.telegram_controller.os.pidfd_open", lambda pid: 55)
    monkeypatch.setattr("kalshi_stats.telegram_controller.signal.pidfd_send_signal", lambda fd, sig: killed.append(fd))
    monkeypatch.setattr("kalshi_stats.telegram_controller.os.close", lambda fd: None)
    states = iter((True, True, False))
    monkeypatch.setattr(owned, "running", lambda: next(states))
    assert owned.stop() == "stopped"
    assert killed == [55]


def test_worker_stop_refuses_unverified_record(tmp_path, monkeypatch):
    path = tmp_path / "worker.json"
    path.write_text(json.dumps({"role": "marker", "pid": 4321, "start_ticks": "old"}))
    owned = OwnedProcess(path, "marker")
    monkeypatch.setattr("kalshi_stats.telegram_controller._process_start_ticks", lambda pid: "new")
    killed = []
    monkeypatch.setattr("kalshi_stats.telegram_controller.signal.pidfd_send_signal", lambda *args: killed.append(args))
    assert owned.stop() == "stale record cleared"
    assert killed == []


@pytest.mark.parametrize("status", [TaskStatus.RUNNING, TaskStatus.VALIDATING, TaskStatus.REVIEWING])
def test_worker_stop_refuses_active_task_ownership(controller, monkeypatch, status):
    value, repo, _ = controller
    task(repo, status=status)
    monkeypatch.setattr(value.worker, "stop", lambda: pytest.fail("must not stop active worker"))
    assert "Worker stop refused: active task ownership" in value.stop_worker()


def test_recover_restarts_safe_stopped_worker(controller, monkeypatch):
    value, _, _ = controller
    monkeypatch.setattr(value.worker, "clear_stale", lambda: True)
    monkeypatch.setattr(value.worker, "running", lambda: False)
    monkeypatch.setattr(value, "start_worker", lambda: "Worker: started")
    assert "worker: started" in value.recover()


def test_recover_refuses_ambiguous_active_ownership(controller, monkeypatch):
    value, repo, _ = controller
    task(repo, status=TaskStatus.RUNNING)
    monkeypatch.setattr(value.worker, "clear_stale", lambda: False)
    monkeypatch.setattr(value.worker, "running", lambda: False)
    assert "ambiguous ownership" in value.recover()


def test_recover_never_bypasses_blocked(controller, monkeypatch):
    value, repo, _ = controller
    task(repo, status=TaskStatus.BLOCKED, blocked_reason="policy")
    monkeypatch.setattr(value.worker, "clear_stale", lambda: False)
    assert "Recovery refused: BLOCKED" in value.recover()


def test_invalid_task_record_fails_health_and_recovery_closed(controller, monkeypatch):
    value, repo, _ = controller
    (repo / "automation" / "tasks" / "corrupt.json").write_text("{not json")
    monkeypatch.setattr(value, "start_worker", lambda: pytest.fail("must not start worker"))
    health = value.health_text()
    assert "Queue accessible: NO (invalid task record)" in health
    assert "Human intervention required: YES" in health
    assert "queue integrity requires human review" in value.recover()


def test_invalid_task_record_refuses_worker_stop(controller, monkeypatch):
    value, repo, _ = controller
    (repo / "automation" / "tasks" / "corrupt.json").write_text("[]")
    monkeypatch.setattr(value.worker, "stop", lambda: pytest.fail("must not stop worker"))
    assert "Worker stop refused: invalid task record" in value.stop_worker()


def test_idea_persists_without_execution(controller, monkeypatch):
    value, _, _ = controller
    monkeypatch.setattr(value, "start_worker", lambda: pytest.fail("must not execute"))
    assert "not executed" in value.handle(123, "/idea examine calibration")
    saved = json.loads(value.paths.ideas.read_text().strip())
    assert saved["text"] == "examine calibration"


def test_terminal_notification_deduplicates(controller):
    value, repo, api = controller
    task(repo, status=TaskStatus.PASSED)
    value.send_notifications()
    value.send_notifications()
    assert len(api.sent) == 1
    assert "Status: PASSED" in api.sent[0]


def test_notification_fires_again_after_task_leaves_and_reenters(controller):
    value, repo, api = controller
    task(repo, status=TaskStatus.FAILED, last_error="first")
    value.send_notifications()
    task(repo, status=TaskStatus.QUEUED)
    value.send_notifications()
    task(repo, status=TaskStatus.FAILED, last_error="second")
    value.send_notifications()
    assert len(api.sent) == 2


def test_network_failure_does_not_mark_notification_delivered(controller):
    value, repo, _ = controller
    task(repo, status=TaskStatus.FAILED, last_error="TEST_FAILURE")
    value.api = FakeAPI(fail=True)
    with pytest.raises(RuntimeError, match="Telegram API request failed"):
        value.send_notifications()
    assert _notifications(value.paths.state) == {}
    value.api = FakeAPI()
    value.send_notifications()
    assert len(value.api.sent) == 1


def test_run_loop_retries_get_updates_with_bounded_backoff(controller, monkeypatch):
    value, _, api = controller
    calls = 0

    def updates(offset):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary network failure")
        if calls == 2:
            return [{"update_id": 7, "message": {"chat": {"id": 123}, "text": "/idea retry once"}}]
        raise KeyboardInterrupt

    api.updates = updates
    sleeps = []
    monkeypatch.setattr("kalshi_stats.telegram_controller.time.sleep", sleeps.append)
    monkeypatch.setattr(value.controller, "adopt_current", lambda: None)
    monkeypatch.setattr(value.controller, "running", lambda: False)
    with pytest.raises(KeyboardInterrupt):
        value.run()
    assert sleeps == [1.0]
    assert len(value.paths.ideas.read_text().splitlines()) == 1
    assert json.loads(value.paths.state.read_text())["update_offset"] == 8


def test_run_loop_retries_notification_without_duplicate_delivery(controller, monkeypatch):
    value, repo, api = controller
    task(repo, status=TaskStatus.FAILED, last_error="temporary delivery test")
    sends = 0
    updates = 0

    def send(text):
        nonlocal sends
        sends += 1
        if sends == 1:
            raise RuntimeError("temporary send failure")
        api.sent.append(text)

    def get_updates(offset):
        nonlocal updates
        updates += 1
        if updates == 3:
            raise KeyboardInterrupt
        return []

    api.send = send
    api.updates = get_updates
    sleeps = []
    monkeypatch.setattr("kalshi_stats.telegram_controller.time.sleep", sleeps.append)
    monkeypatch.setattr(value.controller, "adopt_current", lambda: None)
    monkeypatch.setattr(value.controller, "running", lambda: False)
    with pytest.raises(KeyboardInterrupt):
        value.run()
    assert sleeps == [1.0]
    assert sends == 2
    assert len(api.sent) == 1
    assert _notifications(value.paths.state) == {"one": "FAILED"}


def test_failed_reply_does_not_replay_side_effecting_update(controller):
    value, _, _ = controller
    value.api = FakeAPI(fail=True)
    update = {
        "update_id": 41,
        "message": {"chat": {"id": 123}, "text": "/idea preserve chronology"},
    }
    with pytest.raises(RuntimeError, match="Telegram API request failed"):
        value.process_update(update, 0)
    assert json.loads(value.paths.state.read_text())["update_offset"] == 42
    assert len(value.paths.ideas.read_text().splitlines()) == 1


def _notifications(path):
    return json.loads(path.read_text()).get("notifications", {}) if path.exists() else {}


def test_prolonged_quota_notification(controller):
    value, repo, api = controller
    task(repo, status=TaskStatus.WAITING_FOR_QUOTA, updated_at=NOW)
    value.send_notifications(now=1788181200.0)  # 2026-08-31T13:00:00Z
    assert len(api.sent) == 1
    assert "WAITING_FOR_QUOTA" in api.sent[0]


def test_no_arbitrary_shell_command_path(controller):
    value, _, _ = controller
    assert "/shell" not in value.handle(123, "/help")
    assert value.handle(123, "/exec touch /tmp/no") == "Unknown command. Use /help."
    assert value.handle(123, "/shell whoami") == "Unknown command. Use /help."


def test_token_is_not_emitted_or_persisted(tmp_path):
    secret = "sensitive-unit-test-value"
    env = tmp_path / "telegram.env"
    env.write_text(f"TELEGRAM_BOT_TOKEN={secret}\nTELEGRAM_CHAT_ID=7\n")
    credentials = load_credentials(env)
    seen = []
    def opener(req, timeout):
        seen.append(req.full_url)
        raise OSError("network down " + req.full_url)
    api = TelegramAPI(credentials, opener=opener)
    with pytest.raises(RuntimeError) as captured:
        api.send("hello")
    assert secret not in str(captured.value)
    assert not any(secret in path.read_text(errors="ignore") for path in tmp_path.rglob("*") if path.is_file() and path != env)


def test_c3_pipeline_still_controls_pass_transition():
    source = Path("src/kalshi_stats/automation_dispatcher.py").read_text()
    assert "decision = self.pipeline_call(validating, builder_run)" in source
    assert 'if decision.status == "PASSED"' in source


def test_cli_start_defaults_to_dedicated_host_auth_directory(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    data_root = tmp_path / "host-data"
    repository.mkdir()
    captured = {}

    class FakeOwnedProcess:
        def __init__(self, *_args):
            pass

        def start(self, command, *, log_path):
            captured["command"] = command
            return "started"

    class FakeController:
        def __init__(self, **kwargs):
            captured["worker_command"] = kwargs["worker_command"]

    monkeypatch.setattr(telegram_controller_cli, "OwnedProcess", FakeOwnedProcess)
    monkeypatch.setattr(telegram_controller_cli, "AutomationController", FakeController)
    monkeypatch.setattr(
        telegram_controller_cli, "load_credentials",
        lambda _path: TelegramCredentials("unit-test-placeholder", "123"),
    )

    assert telegram_controller_cli.main([
        "start", "--repository", str(repository), "--data-root", str(data_root),
    ]) == 0

    expected = str((data_root / "codex-home").resolve())
    assert captured["worker_command"][
        captured["worker_command"].index("--auth-directory") + 1
    ] == expected
    assert captured["command"][captured["command"].index("--auth-directory") + 1] == expected
