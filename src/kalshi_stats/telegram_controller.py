"""Host-side, allowlisted Telegram control for Automation V1.

This module deliberately exposes no shell, generic file, or generic process API.
Only controller-owned processes whose PID, birth time, and command marker match a
durable record may be signalled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence
from urllib import parse, request

from .automation_state import TaskRecord, TaskStatus, load_task, utc_now, write_json_atomic


DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "kalshi-stats-automation"
NOTIFY_STATUSES = frozenset({"PASSED", "FAILED", "BLOCKED"})
ACTIVE_STATUSES = frozenset({"RUNNING", "VALIDATING", "REVIEWING"})
QUEUE_STATUSES = frozenset({"QUEUED", *ACTIVE_STATUSES, "WAITING_FOR_QUOTA", "BLOCKED"})
COMMANDS = frozenset({
    "/help", "/status", "/health", "/queue", "/task", "/worker",
    "/worker-start", "/worker-stop", "/recover", "/idea",
})


class QueueIntegrityError(RuntimeError):
    """The durable task queue cannot be interpreted unambiguously."""

    def __init__(self, invalid_names: Sequence[str]) -> None:
        self.invalid_names = tuple(invalid_names)
        super().__init__("invalid task record(s): " + ", ".join(self.invalid_names[:5]))


@dataclass(frozen=True)
class TelegramCredentials:
    token: str
    chat_id: str


@dataclass(frozen=True)
class ControllerPaths:
    data_root: Path = DEFAULT_DATA_ROOT

    @property
    def runtime(self) -> Path:
        return self.data_root / "runtime"

    @property
    def controller_pid(self) -> Path:
        return self.runtime / "controller.pid.json"

    @property
    def worker_pid(self) -> Path:
        return self.runtime / "worker.pid.json"

    @property
    def state(self) -> Path:
        return self.runtime / "controller-state.json"

    @property
    def log(self) -> Path:
        return self.runtime / "controller.log"

    @property
    def ideas(self) -> Path:
        return self.data_root / "ideas.jsonl"


def load_credentials(path: Path) -> TelegramCredentials:
    """Load the two exact Telegram settings without echoing their values."""

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError("Telegram credential file is unavailable") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key in {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}:
            values[key] = value.strip().strip("'\"")
    if not values.get("TELEGRAM_BOT_TOKEN") or not values.get("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Telegram credential file is incomplete")
    return TelegramCredentials(values["TELEGRAM_BOT_TOKEN"], values["TELEGRAM_CHAT_ID"])


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _process_start_ticks(pid: int) -> str | None:
    try:
        # The comm field may contain spaces and parentheses; everything after
        # its final ')' begins with field 3. starttime is field 22.
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return fields[19]
    except (OSError, IndexError, ValueError):
        return None


def _process_cmdline(pid: int) -> tuple[str, ...]:
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ()
    return tuple(part.decode(errors="replace") for part in raw.split(b"\0") if part)


class OwnedProcess:
    """Manage one fixed-role process, never a caller-supplied PID."""

    def __init__(self, record_path: Path, marker: str) -> None:
        self.record_path = record_path
        self.marker = marker

    def record(self) -> Mapping[str, Any] | None:
        value = _read_json(self.record_path, None)
        return value if isinstance(value, dict) else None

    def running(self) -> bool:
        record = self.record()
        if not record or record.get("role") != self.marker:
            return False
        pid = record.get("pid")
        if not isinstance(pid, int) or pid <= 1:
            return False
        return (
            _process_start_ticks(pid) == record.get("start_ticks")
            and self.marker in _process_cmdline(pid)
        )

    def clear_stale(self) -> bool:
        if self.record_path.exists() and not self.running():
            self.record_path.unlink(missing_ok=True)
            return True
        return False

    def adopt_current(self) -> None:
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        ticks = _process_start_ticks(os.getpid())
        if ticks is None:
            raise RuntimeError("cannot establish controller process identity")
        write_json_atomic(self.record_path, {
            "role": self.marker, "pid": os.getpid(), "start_ticks": ticks,
            "started_at": utc_now(),
        })

    def start(
        self, command: Sequence[str], *, log_path: Path, cwd: Path | None = None,
    ) -> str:
        if self.running():
            return "already running"
        self.clear_stale()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab", buffering=0) as stream:
            process = subprocess.Popen(
                tuple(command), stdin=subprocess.DEVNULL, stdout=stream,
                stderr=stream, start_new_session=True, close_fds=True,
                cwd=cwd,
            )
        ticks = _process_start_ticks(process.pid)
        if ticks is None:
            raise RuntimeError("started process identity is unavailable")
        write_json_atomic(self.record_path, {
            "role": self.marker, "pid": process.pid, "start_ticks": ticks,
            "started_at": utc_now(),
        })
        return "started"

    def stop(self, *, timeout: float = 5.0) -> str:
        if not self.running():
            stale = self.clear_stale()
            return "stale record cleared" if stale else "not running"
        record = self.record() or {}
        pid = int(record["pid"])
        try:
            pidfd = os.pidfd_open(pid)
        except (AttributeError, OSError):
            return "stop refused; stable process handle unavailable"
        try:
            # Recheck after opening the stable kernel handle. If PID reuse raced
            # with us, refuse rather than signalling the replacement process.
            if not self.running():
                return "stop refused; process identity changed"
            signal.pidfd_send_signal(pidfd, signal.SIGTERM)
        finally:
            os.close(pidfd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running():
                self.record_path.unlink(missing_ok=True)
                return "stopped"
            time.sleep(0.05)
        # Fail closed. Never escalate to an unverified or reused PID.
        return "stop requested; process still running"


class TelegramAPI:
    """Small Telegram HTTP adapter that never puts the token in diagnostics."""

    def __init__(self, credentials: TelegramCredentials, *, opener: Callable[..., Any] = request.urlopen) -> None:
        self.credentials = credentials
        self._opener = opener

    def call(self, method: str, fields: Mapping[str, str], *, timeout: float = 30.0) -> Mapping[str, Any]:
        url = f"https://api.telegram.org/bot{self.credentials.token}/{method}"
        req = request.Request(url, data=parse.urlencode(fields).encode(), method="POST")
        try:
            with self._opener(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode())
        except Exception as exc:
            # Exception strings may include the token-bearing URL.
            raise RuntimeError("Telegram API request failed") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError("Telegram API returned an unsuccessful response")
        return payload

    def send(self, text: str) -> None:
        self.call("sendMessage", {"chat_id": self.credentials.chat_id, "text": text[:3500]})

    def updates(self, offset: int) -> list[Mapping[str, Any]]:
        payload = self.call("getUpdates", {"offset": str(offset), "timeout": "25"}, timeout=35)
        result = payload.get("result", [])
        return result if isinstance(result, list) else []


class AutomationController:
    def __init__(
        self, *, repository: Path, paths: ControllerPaths,
        credentials: TelegramCredentials, worker_command: Sequence[str],
        api: TelegramAPI | None = None,
        docker_check: Callable[[], bool] | None = None,
        quota_notify_after: float = 1800.0,
    ) -> None:
        self.repository = repository.resolve()
        self.paths = paths
        self.credentials = credentials
        self.worker_command = tuple(worker_command)
        self.api = api or TelegramAPI(credentials)
        self.worker = OwnedProcess(paths.worker_pid, "kalshi_stats.automation_cli")
        self.controller = OwnedProcess(paths.controller_pid, "kalshi_stats.telegram_controller_cli")
        self.docker_check = docker_check or docker_available
        self.quota_notify_after = quota_notify_after
        paths.runtime.mkdir(parents=True, exist_ok=True)

    def tasks(self) -> list[TaskRecord]:
        tasks = []
        invalid = []
        for path in sorted((self.repository / "automation" / "tasks").glob("*.json")):
            try:
                tasks.append(load_task(path))
            except Exception:
                invalid.append(path.name)
        if invalid:
            raise QueueIntegrityError(invalid)
        return tasks

    def _task(self, task_id: str) -> TaskRecord | None:
        if not task_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in task_id):
            return None
        return next((task for task in self.tasks() if task.task_id == task_id), None)

    def worker_status(self) -> str:
        return "RUNNING (controller-owned)" if self.worker.running() else "STOPPED"

    def status_text(self) -> str:
        try:
            tasks = self.tasks()
        except QueueIntegrityError as exc:
            return f"Automation: worker {self.worker_status()}\nTasks: INVALID ({exc})"
        counts = {status: sum(task.status.value == status for task in tasks) for status in QUEUE_STATUSES}
        active = ", ".join(f"{name}={counts[name]}" for name in ("QUEUED", "RUNNING", "VALIDATING", "REVIEWING", "WAITING_FOR_QUOTA", "BLOCKED"))
        return f"Automation: worker {self.worker_status()}\nTasks: {active}"

    def health_text(self) -> str:
        queue_ok = (self.repository / "automation" / "tasks").is_dir()
        queue_detail = "YES" if queue_ok else "NO"
        try:
            tasks = self.tasks() if queue_ok else []
        except QueueIntegrityError:
            tasks = []
            queue_ok = False
            queue_detail = "NO (invalid task record)"
        docker_ok = self.docker_check()
        needs_human = not queue_ok or not docker_ok or any(
            task.status is TaskStatus.BLOCKED for task in tasks
        )
        if any(task.status.value in ACTIVE_STATUSES for task in tasks) and not self.worker.running():
            needs_human = True
        return "\n".join((
            "Telegram controller: RUNNING",
            f"Automation worker: {self.worker_status()}",
            f"Docker available: {'YES' if docker_ok else 'NO'}",
            f"Queue accessible: {queue_detail}",
            f"Human intervention required: {'YES' if needs_human else 'NO'}",
        ))

    def queue_text(self) -> str:
        try:
            selected = [task for task in self.tasks() if task.status.value in QUEUE_STATUSES]
        except QueueIntegrityError as exc:
            return f"Queue unavailable: {exc}. Human review required."
        if not selected:
            return "Queue: no active, waiting, or blocked tasks."
        lines = ["Queue:"]
        for task in selected[:20]:
            lines.append(f"{task.task_id}: {task.status.value}")
        if len(selected) > 20:
            lines.append(f"... and {len(selected) - 20} more")
        return "\n".join(lines)

    def task_text(self, task_id: str) -> str:
        try:
            task = self._task(task_id)
        except QueueIntegrityError as exc:
            return f"Task unavailable: {exc}. Human review required."
        if task is None:
            return "Task not found. Use /task <task-id>."
        action = (task.next_action or "No next action recorded.").replace("\n", " ")[:500]
        return f"{task.task_id} — {task.title}\nStatus: {task.status.value}\nNext: {action}"

    def _worker_start_refusal(self) -> str | None:
        try:
            tasks = self.tasks()
        except QueueIntegrityError as exc:
            return f"{exc}; queue integrity requires human review."
        blocked = [task.task_id for task in tasks if task.status is TaskStatus.BLOCKED]
        if blocked:
            return (
                "BLOCKED state requires human review "
                f"({', '.join(blocked[:5])})."
            )
        active = [task.task_id for task in tasks if task.status.value in ACTIVE_STATUSES]
        if active and not self.worker.running():
            return (
                "ambiguous ownership for active task(s) "
                f"{', '.join(active[:5])}."
            )
        if not self.docker_check():
            return "Docker is unavailable."
        return None

    def start_worker(self) -> str:
        if self.worker.running():
            return "Worker: already running"
        refusal = self._worker_start_refusal()
        if refusal:
            return f"Worker start refused: {refusal}"
        return f"Worker: {self.worker.start(self.worker_command, log_path=self.paths.log, cwd=self.repository)}"

    def stop_worker(self) -> str:
        try:
            active = [task.task_id for task in self.tasks() if task.status.value in ACTIVE_STATUSES]
        except QueueIntegrityError as exc:
            return f"Worker stop refused: {exc}; human review required."
        if active:
            return (
                "Worker stop refused: active task ownership must remain with the dispatcher "
                f"({', '.join(active[:5])})."
            )
        return f"Worker: {self.worker.stop()}"

    def recover(self) -> str:
        controller_stale = self.controller.clear_stale()
        stale = self.worker.clear_stale()
        refusal = self._worker_start_refusal()
        if refusal:
            return f"Recovery refused: {refusal}"
        if self.worker.running():
            return (
                "Recovery complete: worker already running; "
                f"stale record cleared={'YES' if stale or controller_stale else 'NO'}."
            )
        result = self.start_worker()
        return (
            f"Recovery complete: {result.lower()}; "
            f"stale record cleared={'YES' if stale or controller_stale else 'NO'}."
        )

    def persist_idea(self, text: str) -> str:
        idea = text.strip()
        if not idea:
            return "Usage: /idea <text>"
        self.paths.ideas.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.ideas.open("a", encoding="utf-8") as stream:
            json.dump({"created_at": utc_now(), "source": "telegram", "text": idea[:2000]}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return "Idea saved for later planning; it was not executed."

    def handle(self, chat_id: object, text: object) -> str | None:
        if str(chat_id) != self.credentials.chat_id or not isinstance(text, str):
            return None
        command, _, argument = text.strip().partition(" ")
        command = command.split("@", 1)[0].lower()
        if command not in COMMANDS:
            return "Unknown command. Use /help."
        if command == "/help":
            return "Commands: /status /health /queue /task <id> /worker /worker-start /worker-stop /recover /idea <text>"
        if command == "/status":
            return self.status_text()
        if command == "/health":
            return self.health_text()
        if command == "/queue":
            return self.queue_text()
        if command == "/task":
            return self.task_text(argument.strip())
        if command == "/worker":
            return f"Worker: {self.worker_status()}"
        if command == "/worker-start":
            return self.start_worker()
        if command == "/worker-stop":
            return self.stop_worker()
        if command == "/recover":
            return self.recover()
        return self.persist_idea(argument)

    def notification_candidates(self, *, now: float | None = None) -> list[tuple[str, str]]:
        timestamp = time.time() if now is None else now
        state = _read_json(self.paths.state, {})
        delivered = state.get("notifications", {}) if isinstance(state, dict) else {}
        candidates = []
        for task in self.tasks():
            status = task.status.value
            eligible = status in NOTIFY_STATUSES
            if status == "WAITING_FOR_QUOTA":
                try:
                    updated = datetime.fromisoformat(task.updated_at.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    updated = timestamp
                eligible = timestamp - updated >= self.quota_notify_after
            if eligible and delivered.get(task.task_id) != status:
                reason = task.blocked_reason or task.last_error or task.next_action or "Review task state."
                message = f"{task.task_id} — {task.title}\nStatus: {status}\nNext: {reason.replace(chr(10), ' ')[:500]}"
                candidates.append((task.task_id, message))
        return candidates

    def send_notifications(self, *, now: float | None = None) -> None:
        state = _read_json(self.paths.state, {})
        if not isinstance(state, dict):
            state = {}
        delivered = dict(state.get("notifications", {}))
        tasks = {task.task_id: task for task in self.tasks()}
        # Clear an old delivery marker only after the task visibly leaves that
        # state, so a later FAILED/BLOCKED re-entry is a new transition.
        for task_id, delivered_status in tuple(delivered.items()):
            current = tasks.get(task_id)
            if current is None or current.status.value != delivered_status:
                delivered.pop(task_id, None)
        state["notifications"] = delivered
        write_json_atomic(self.paths.state, state)
        for task_id, message in self.notification_candidates(now=now):
            self.api.send(message)  # Persist only after confirmed delivery.
            task = tasks[task_id]
            delivered[task_id] = task.status.value
            state["notifications"] = delivered
            write_json_atomic(self.paths.state, state)

    def process_update(self, update: Mapping[str, Any], offset: int) -> int:
        """Apply one update once, durably consuming it before sending a reply."""

        update_id = update.get("update_id")
        message = update.get("message", {})
        reply = self.handle(message.get("chat", {}).get("id"), message.get("text"))
        if isinstance(update_id, int):
            offset = max(offset, update_id + 1)
            current = _read_json(self.paths.state, {})
            current = current if isinstance(current, dict) else {}
            current["update_offset"] = offset
            write_json_atomic(self.paths.state, current)
        if reply is not None:
            # Side effects and their update offset are durable first. A failed
            # reply must not replay /idea or process-control commands.
            self.api.send(reply)
        return offset

    def run(self) -> None:
        self.controller.adopt_current()
        state = _read_json(self.paths.state, {})
        offset = int(state.get("update_offset", 0)) if isinstance(state, dict) else 0
        backoff = 1.0
        try:
            while True:
                try:
                    for update in self.api.updates(offset):
                        offset = self.process_update(update, offset)
                    self.send_notifications()
                    backoff = 1.0
                except RuntimeError:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)
        finally:
            if self.controller.running():
                self.paths.controller_pid.unlink(missing_ok=True)


def build_worker_command(
    repository: Path, *, worktree_root: Path, primary_runtime: Path,
    auth_directory: Path, image: str, base_branch: str,
) -> tuple[str, ...]:
    return (
        sys.executable, "-m", "kalshi_stats.automation_cli", "run", "--continuous",
        "--worktree-root", str(worktree_root),
        "--primary-runtime-worktree", str(primary_runtime),
        "--auth-directory", str(auth_directory),
        "--base-branch", base_branch,
        "--image", image,
    )


def docker_available() -> bool:
    """Check the daemon with one fixed, read-only command."""

    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ("docker", "info", "--format", "{{.ServerVersion}}"),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
