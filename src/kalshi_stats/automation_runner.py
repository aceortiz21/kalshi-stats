"""Isolated, noninteractive Codex execution foundation for Automation V1.

The host runner validates an already-created task worktree, creates durable run
files, and launches one bounded Codex process inside the Phase B container.  It
does not choose tasks, retry failures, validate research, merge branches, or
interact with the live Kalshi runtime.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from typing import Any, Mapping, Sequence

from .automation_state import (
    ErrorClassification,
    RunRecord,
    TaskRecord,
    TaskStatus,
    is_valid_task_branch,
    load_run,
    load_task,
    save_run,
    utc_now,
    write_json_atomic,
)
from .automation_quota import run_with_quota_wait


CONTAINER_WORKTREE = Path("/workspace")
CONTAINER_CODEX_HOME = Path("/codex-home")
DEFAULT_IMAGE = "kalshi-stats-automation:phase-b-v1"
DEFAULT_TIMEOUT_SECONDS = 7_200

RUN_FILENAMES = (
    "HANDOFF.md",
    "state.json",
    "events.jsonl",
    "final.md",
    "validation.json",
    "errors.log",
)

# Values are forwarded only when the caller explicitly requests their names.
# Proxy variables are allowed because Docker Desktop and CI commonly need them
# for outbound package installation. Values never appear in diagnostics.
ALLOWED_CONTAINER_ENVIRONMENT = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "all_proxy",
        "LANG",
        "LC_ALL",
        "TZ",
        "TERM",
    }
)

_CREDENTIAL_NAME = re.compile(
    r"(?:^|_)(?:API|ACCESS|AUTH|PRIVATE|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|KEY)(?:_|$)",
    re.IGNORECASE,
)
_RATE_LIMIT = re.compile(r"rate.?limit|too many requests|\b429\b|quota", re.I)
_CONTEXT_LIMIT = re.compile(
    r"context (?:window|length)|maximum context|max(?:imum)? tokens|context exhausted",
    re.I,
)
_TEST_FAILURE = re.compile(r"(?:pytest|test(?:s)?)\b.*(?:failed|failure)|assertionerror", re.I)
_CODE_FAILURE = re.compile(
    r"syntaxerror|compile(?:all)?\b.*failed|patch (?:failed|does not apply)|typeerror",
    re.I,
)
_INFRASTRUCTURE_FAILURE = re.compile(
    r"docker daemon|cannot connect to docker|no such image|network is unreachable|"
    r"temporary failure in name resolution|timed out|timeout expired|executable not found|"
    r"no prompt provided via stdin",
    re.I,
)
_SECURITY_VIOLATION = re.compile(r"security_violation|security boundary|permission denied by policy", re.I)
_DATABASE_FAILURE = re.compile(
    r"database_integrity_failure|database disk image is malformed|integrity_check.*(?:fail|not ok)",
    re.I,
)
_BEARER_SECRET = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_OPENAI_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"password|passwd|private[_-]?key|credential)\b\s*[:=]\s*[\"']?)[^\s,\"'}]+"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
REDACTION = "[REDACTED]"


class RunnerFailure(RuntimeError):
    """A runner failure with a durable Phase A classification."""

    def __init__(self, message: str, classification: ErrorClassification) -> None:
        super().__init__(message)
        self.classification = classification


class SecurityViolation(RunnerFailure):
    """A fail-closed worktree, mount, branch, or environment violation."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorClassification.SECURITY_VIOLATION)


@dataclass(frozen=True)
class WorktreeIdentity:
    path: Path
    branch: str
    git_dir: Path
    git_common_dir: Path


@dataclass(frozen=True)
class RunnerConfig:
    allowed_worktree_root: Path
    primary_runtime_worktree: Path
    prompt_path: Path
    run_directory: Path
    auth_directory: Path
    image: str = DEFAULT_IMAGE
    model: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    environment_names: tuple[str, ...] = ()
    dry_run: bool = False
    success_status: TaskStatus = TaskStatus.VALIDATING
    success_next_action: str = "Run the separate mechanical validation and review pipeline."

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least one")
        if not self.image.strip():
            raise ValueError("image must be non-empty")
        if self.model is not None and not self.model.strip():
            raise ValueError("model must be non-empty when supplied")
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            raise ValueError("unsupported reasoning_effort")


@dataclass(frozen=True)
class LaunchPlan:
    task: TaskRecord
    run: RunRecord
    worktree: WorktreeIdentity
    config: RunnerConfig
    docker_command: tuple[str, ...]
    codex_command: tuple[str, ...]
    bootstrap_prompt: str
    forwarded_environment: Mapping[str, str]
    container_name: str
    git_bundle_path: Path

    def diagnostic(self) -> dict[str, Any]:
        """Return launch metadata that intentionally excludes environment values."""

        auth_path = str(self.config.auth_directory.resolve())
        docker_command = [
            argument.replace(auth_path, "<dedicated-automation-auth>")
            for argument in self.docker_command
        ]

        return {
            "dry_run": self.config.dry_run,
            "task_id": self.task.task_id,
            "run_id": self.run.run_id,
            "branch": self.worktree.branch,
            "worktree": str(self.worktree.path),
            "image": self.config.image,
            "timeout_seconds": self.config.timeout_seconds,
            "docker_command": docker_command,
            "codex_command": list(self.codex_command),
            "forwarded_environment_names": sorted(self.forwarded_environment),
            "mounts": [
                {"source": str(self.worktree.path), "target": str(CONTAINER_WORKTREE)},
                {
                    "source": "<dedicated-automation-auth>",
                    "target": str(CONTAINER_CODEX_HOME),
                    "writable": True,
                },
            ],
        }


def redact_diagnostic_text(text: str, *, sensitive_values: Sequence[str] = ()) -> str:
    """Redact configured paths and credential-shaped values before persistence."""

    redacted = text
    for value in sorted((value for value in sensitive_values if value), key=len, reverse=True):
        redacted = redacted.replace(value, REDACTION)
    redacted = _PRIVATE_KEY_BLOCK.sub(REDACTION, redacted)
    redacted = _BEARER_SECRET.sub(r"\1" + REDACTION, redacted)
    redacted = _OPENAI_SECRET.sub(REDACTION, redacted)
    redacted = _CREDENTIAL_ASSIGNMENT.sub(r"\1" + REDACTION, redacted)
    return redacted


def _run_git(worktree: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ("git", "-C", str(worktree), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise SecurityViolation(f"Git worktree verification failed: {detail}") from exc
    return result.stdout.strip()


def _registered_worktrees(porcelain: str) -> dict[Path, str | None]:
    registered: dict[Path, str | None] = {}
    current_path: Path | None = None
    current_branch: str | None = None
    for line in (*porcelain.splitlines(), ""):
        if not line:
            if current_path is not None:
                registered[current_path.resolve()] = current_branch
            current_path = None
            current_branch = None
        elif line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/"):
            current_branch = line.removeprefix("branch refs/heads/")
    return registered


def verify_task_worktree(
    task: TaskRecord,
    run: RunRecord,
    *,
    allowed_worktree_root: Path,
    primary_runtime_worktree: Path,
) -> WorktreeIdentity:
    """Verify the task against Git's registered worktree data and fail closed."""

    try:
        worktree = Path(task.worktree).resolve(strict=True)
        allowed_root = allowed_worktree_root.resolve(strict=True)
        primary = primary_runtime_worktree.resolve(strict=True)
    except OSError as exc:
        raise SecurityViolation(f"Required worktree boundary path is unavailable: {exc}") from exc

    broad_roots = {Path("/"), Path("/home"), Path.home().resolve()}
    if allowed_root in broad_roots:
        raise SecurityViolation("permitted automation-worktree root is too broad")
    if not worktree.is_dir():
        raise SecurityViolation("task worktree is not a directory")
    if not worktree.is_relative_to(allowed_root):
        raise SecurityViolation("task worktree escapes the permitted automation-worktree root")
    if worktree == primary:
        raise SecurityViolation("primary runtime worktree is forbidden")
    if Path(run.worktree).resolve(strict=False) != worktree:
        raise SecurityViolation("TaskRecord and RunRecord worktrees disagree")
    if task.branch != run.branch:
        raise SecurityViolation("TaskRecord and RunRecord branches disagree")
    if not is_valid_task_branch(task.branch):
        raise SecurityViolation("branch is not a valid autonomous task branch")

    top_level = Path(_run_git(worktree, "rev-parse", "--show-toplevel")).resolve()
    if top_level != worktree:
        raise SecurityViolation("task path is not the root of its Git worktree")

    branch = _run_git(worktree, "branch", "--show-current")
    if branch != task.branch:
        raise SecurityViolation("recorded branch does not match Git's current branch")
    if branch in {"main", "automation-integration"}:
        raise SecurityViolation(f"{branch} is not permitted as a task branch")

    registered = _registered_worktrees(_run_git(worktree, "worktree", "list", "--porcelain"))
    if registered.get(worktree) != branch:
        raise SecurityViolation("task path/branch is not a registered Git worktree pair")

    primary_top = Path(_run_git(primary, "rev-parse", "--show-toplevel")).resolve()
    if primary_top != primary:
        raise SecurityViolation("configured primary runtime path is not a worktree root")

    git_dir = Path(_run_git(worktree, "rev-parse", "--path-format=absolute", "--git-dir")).resolve()
    common_dir = Path(
        _run_git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    ).resolve()
    primary_common = Path(
        _run_git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir")
    ).resolve()
    if common_dir != primary_common:
        raise SecurityViolation("task and primary paths do not belong to the same Git repository")

    return WorktreeIdentity(worktree, branch, git_dir, common_dir)


def select_container_environment(
    source: Mapping[str, str], requested_names: Sequence[str]
) -> dict[str, str]:
    """Select explicit noncredential environment names without logging values."""

    selected: dict[str, str] = {}
    for name in requested_names:
        if _CREDENTIAL_NAME.search(name) or name.upper().startswith("KALSHI_"):
            raise SecurityViolation(f"credential-like environment variable is forbidden: {name}")
        if name not in ALLOWED_CONTAINER_ENVIRONMENT:
            raise SecurityViolation(f"environment variable is not allowlisted: {name}")
        if name in source:
            selected[name] = source[name]
    return selected


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _initial_handoff(task: TaskRecord, run: RunRecord) -> str:
    return f"""# Automation Run Handoff

- Task: `{task.task_id}` — {task.title}
- Run: `{run.run_id}`
- Objective: {task.objective}
- Branch: `{run.branch}`
- Worktree: `{run.worktree}`
- Completed work: Run directory initialized; no Codex execution recorded yet.
- Remaining work: Execute the bounded task and validate its result.
- Decisions/assumptions: Container isolation is the outer security boundary.
- Files changed: None recorded.
- Last validation status: No validation recorded.
- Active blocker/error classification: None.
- Exact next action: {run.next_action}
"""


def create_run_directory(path: Path, task: TaskRecord, run: RunRecord) -> None:
    """Create the six-file Phase A run contract without overwriting a prior run."""

    if path.name != run.run_id:
        raise SecurityViolation("run directory name must exactly match run_id")
    if path.exists():
        if not path.is_dir():
            raise SecurityViolation("run directory path exists but is not a directory")
        missing = [name for name in RUN_FILENAMES if not (path / name).is_file()]
        if missing:
            raise RunnerFailure(
                f"existing run directory is incomplete: {', '.join(missing)}",
                ErrorClassification.INFRASTRUCTURE_FAILURE,
            )
        if load_run(path / "state.json") != run:
            raise SecurityViolation("existing run state disagrees with supplied RunRecord")
        return

    path.mkdir(parents=True, exist_ok=False)
    _write_text_atomic(path / "HANDOFF.md", _initial_handoff(task, run))
    save_run(path / "state.json", run)
    _write_text_atomic(path / "events.jsonl", "")
    _write_text_atomic(path / "final.md", "")
    write_json_atomic(path / "validation.json", {})
    _write_text_atomic(path / "errors.log", "")


def create_recovery_bootstrap(
    task: TaskRecord,
    run: RunRecord,
    *,
    task_json_path: Path,
    run_directory: Path,
) -> str:
    """Create a complete fresh-context prompt independent of conversation state."""

    task_json = task_json_path.relative_to(Path(task.worktree))
    run_relative = run_directory.relative_to(Path(task.worktree))
    return f"""You are beginning a fresh autonomous development context for task `{task.task_id}`,
run `{run.run_id}`. Previous conversation context is unavailable and must not be assumed.

Before changing anything, read these sources completely and reconcile them:
1. AGENTS.md
2. README.md
3. docs/RESEARCH_SYSTEM.md
4. docs/CODEX_HANDOFF.md
5. docs/AUTOMATION_CHECKLIST.md
6. {task_json.as_posix()} (the task JSON)
7. {(run_relative / 'HANDOFF.md').as_posix()} (the current HANDOFF.md)
8. Run `git status --short`, inspect `git diff`, and inspect relevant recent `git log`.
9. Read {(run_relative / 'validation.json').as_posix()} (prior validation.json).

The task and policy files define your authority. Preserve chronology, negative evidence,
and existing user changes. Main is human-only. Do not merge to main, access the primary
runtime, use write-capable Kalshi credentials, enable real-money execution, or access a
Docker daemon. Update durable run files as work progresses so a future fresh context can
continue safely.

Current objective: {task.objective}
Current next action: {run.next_action}
"""


def _contained_file(path: Path, root: Path, description: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise SecurityViolation(f"{description} is unavailable: {exc}") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root_resolved):
        raise SecurityViolation(f"{description} must be a file inside the task worktree")
    return resolved


def _container_path(host_path: Path, worktree: Path) -> Path:
    return CONTAINER_WORKTREE / host_path.relative_to(worktree)


def build_codex_command(
    *,
    final_path: Path,
    model: str | None,
    reasoning_effort: str | None,
) -> tuple[str, ...]:
    """Build syntax verified against Codex CLI 0.151.0."""

    command = [
        "codex",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "danger-full-access",
        "exec",
        "--json",
        "--color",
        "never",
        "--ignore-user-config",
        "--cd",
        str(CONTAINER_WORKTREE),
        "--output-last-message",
        str(final_path),
    ]
    if model is not None:
        command.extend(("--model", model))
    if reasoning_effort is not None:
        command.extend(("--config", f'model_reasoning_effort="{reasoning_effort}"'))
    command.append("-")
    return tuple(command)


def _validate_mount_source(path: Path, *, must_be_outside: Path | None = None) -> Path:
    if path.is_symlink():
        raise SecurityViolation("mount source must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SecurityViolation(f"mount source is unavailable: {exc}") from exc
    if not resolved.is_dir():
        raise SecurityViolation("mount source must be a real directory")
    if must_be_outside is not None and resolved.is_relative_to(must_be_outside.resolve()):
        raise SecurityViolation("dedicated Codex auth directory must be outside the task worktree")
    return resolved


def prepare_launch(
    task: TaskRecord,
    run: RunRecord,
    config: RunnerConfig,
    *,
    source_environment: Mapping[str, str] | None = None,
) -> LaunchPlan:
    if task.task_id != run.task_id:
        raise SecurityViolation("TaskRecord and RunRecord task IDs disagree")
    worktree = verify_task_worktree(
        task,
        run,
        allowed_worktree_root=config.allowed_worktree_root,
        primary_runtime_worktree=config.primary_runtime_worktree,
    )
    expected_run_directory = worktree.path / "automation" / "runs" / run.run_id
    if config.run_directory.resolve(strict=False) != expected_run_directory.resolve(strict=False):
        raise SecurityViolation("run directory must be automation/runs/<run-id> in the task worktree")
    prompt_path = _contained_file(config.prompt_path, worktree.path, "prompt path")
    task_json_path = _contained_file(
        worktree.path / "automation" / "tasks" / f"{task.task_id}.json",
        worktree.path,
        "task JSON",
    )
    if load_task(task_json_path) != task:
        raise SecurityViolation("loaded TaskRecord disagrees with canonical task JSON")
    auth_directory = _validate_mount_source(
        config.auth_directory, must_be_outside=worktree.path
    )
    home = Path.home().resolve()
    forbidden_auth_roots = (
        Path("/"),
        Path("/home"),
        home,
        home / ".ssh",
        home / ".aws",
        home / ".config",
        home / ".codex",
        config.primary_runtime_worktree.resolve(strict=True),
    )
    if any(
        auth_directory == forbidden
        or (forbidden not in {Path("/"), Path("/home"), home} and auth_directory.is_relative_to(forbidden))
        for forbidden in forbidden_auth_roots
    ):
        raise SecurityViolation("broad home/root authentication mounts are forbidden")

    forwarded = select_container_environment(
        source_environment if source_environment is not None else os.environ,
        config.environment_names,
    )
    bootstrap = create_recovery_bootstrap(
        task,
        run,
        task_json_path=task_json_path,
        run_directory=expected_run_directory,
    )
    user_prompt = prompt_path.read_text(encoding="utf-8")
    bootstrap = f"{bootstrap}\n\n# Bounded task prompt\n\n{user_prompt}"
    run_relative = expected_run_directory.relative_to(worktree.path)
    expected_final = run_relative / "final.md"
    expected_events = run_relative / "events.jsonl"
    for recorded, expected, description in (
        (run.final_response_path, expected_final, "final response path"),
        (run.jsonl_log_path, expected_events, "JSONL log path"),
    ):
        recorded_path = Path(recorded)
        normalized = (
            recorded_path.resolve(strict=False)
            if recorded_path.is_absolute()
            else (worktree.path / recorded_path).resolve(strict=False)
        )
        if normalized != (worktree.path / expected).resolve(strict=False):
            raise SecurityViolation(f"RunRecord {description} disagrees with run directory")
    final_container_path = _container_path(expected_run_directory / "final.md", worktree.path)
    codex_command = build_codex_command(
        final_path=final_container_path,
        model=config.model,
        reasoning_effort=config.reasoning_effort,
    )
    container_name = f"kalshi-auto-{run.run_id.lower()}"
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", container_name):
        raise SecurityViolation("run_id cannot form a safe Docker container name")

    docker_command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--interactive",
        "--name",
        container_name,
        "--network",
        "bridge",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "1024",
        "--memory",
        "8g",
        "--cpus",
        "4",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=2g",
        "--mount",
        f"type=bind,src={worktree.path},dst={CONTAINER_WORKTREE}",
        "--mount",
        f"type=bind,src={auth_directory},dst={CONTAINER_CODEX_HOME}",
        "--workdir",
        str(CONTAINER_WORKTREE),
        "--env",
        f"CODEX_HOME={CONTAINER_CODEX_HOME}",
        "--env",
        "HOME=/tmp/automation-home",
        "--env",
        f"AUTOMATION_TASK_BRANCH={worktree.branch}",
        "--env",
        f"AUTOMATION_RUN_RELATIVE={run_relative.as_posix()}",
    ]
    proxy_names = {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "all_proxy",
    }
    for name in sorted(proxy_names):
        docker_command.extend(("--env", name if name in forwarded else f"{name}="))
    for name in sorted(set(forwarded) - proxy_names):
        docker_command.extend(("--env", name))
    docker_command.extend((config.image, *codex_command))
    bundle = expected_run_directory / ".repository.bundle"
    create_run_directory(expected_run_directory, task, run)
    return LaunchPlan(
        task=task,
        run=run,
        worktree=worktree,
        config=replace(config, auth_directory=auth_directory, prompt_path=prompt_path),
        docker_command=tuple(docker_command),
        codex_command=codex_command,
        bootstrap_prompt=bootstrap,
        forwarded_environment=forwarded,
        container_name=container_name,
        git_bundle_path=bundle,
    )


def classify_runner_result(
    exit_code: int,
    diagnostic_text: str = "",
    *,
    timed_out: bool = False,
) -> ErrorClassification:
    if exit_code == 0 and not timed_out:
        return ErrorClassification.SUCCESS
    if _SECURITY_VIOLATION.search(diagnostic_text):
        return ErrorClassification.SECURITY_VIOLATION
    if _DATABASE_FAILURE.search(diagnostic_text):
        return ErrorClassification.DATABASE_INTEGRITY_FAILURE
    if _RATE_LIMIT.search(diagnostic_text):
        return ErrorClassification.RATE_LIMITED
    if _CONTEXT_LIMIT.search(diagnostic_text):
        return ErrorClassification.CONTEXT_EXHAUSTED
    if timed_out or _INFRASTRUCTURE_FAILURE.search(diagnostic_text):
        return ErrorClassification.INFRASTRUCTURE_FAILURE
    if _TEST_FAILURE.search(diagnostic_text):
        return ErrorClassification.TEST_FAILURE
    if _CODE_FAILURE.search(diagnostic_text):
        return ErrorClassification.CODE_FAILURE
    return ErrorClassification.UNKNOWN_FAILURE


def _append_event(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _stream_redacted(
    source: Any,
    destination: Any,
    *,
    sensitive_values: Sequence[str],
) -> None:
    try:
        for line in source:
            destination.write(
                redact_diagnostic_text(line, sensitive_values=sensitive_values)
            )
            destination.flush()
    finally:
        source.close()


def _redact_file(path: Path, *, sensitive_values: Sequence[str]) -> None:
    if not path.is_file():
        return
    original = path.read_text(encoding="utf-8", errors="replace")
    redacted = redact_diagnostic_text(original, sensitive_values=sensitive_values)
    if redacted != original:
        _write_text_atomic(path, redacted)


def _extract_thread_id(events_path: Path) -> str | None:
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        for key in ("thread_id", "session_id"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        thread = event.get("thread")
        if isinstance(thread, dict) and isinstance(thread.get("id"), str):
            return thread["id"]
    return None


def _create_git_bundle(plan: LaunchPlan) -> None:
    temporary = plan.git_bundle_path.with_name(f".{plan.git_bundle_path.name}.tmp")
    try:
        if temporary.exists():
            temporary.unlink()
        subprocess.run(
            (
                "git",
                "-C",
                str(plan.worktree.path),
                "bundle",
                "create",
                str(temporary),
                f"refs/heads/{plan.worktree.branch}",
            ),
            check=True,
            capture_output=True,
            text=True,
        )
        os.replace(temporary, plan.git_bundle_path)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise RunnerFailure(
            f"failed to create isolated Git bundle: {detail}",
            ErrorClassification.INFRASTRUCTURE_FAILURE,
        ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def execute_launch(plan: LaunchPlan) -> dict[str, Any]:
    """Execute one prepared launch, or return its redacted dry-run diagnostic."""

    diagnostic = plan.diagnostic()
    if plan.config.dry_run:
        return diagnostic

    _create_git_bundle(plan)
    run_dir = plan.config.run_directory
    events_path = run_dir / "events.jsonl"
    errors_path = run_dir / "errors.log"
    started_at = utc_now()
    _append_event(
        events_path,
        {
            "type": "runner.launch",
            "timestamp": started_at,
            "metadata": diagnostic,
        },
    )
    timed_out = False
    sensitive_values = (str(plan.config.auth_directory.resolve()),)
    with events_path.open("a", encoding="utf-8") as stdout, errors_path.open(
        "a", encoding="utf-8"
    ) as stderr:
        try:
            process = subprocess.Popen(
                plan.docker_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise RunnerFailure(
                f"failed to launch Docker: {exc}",
                ErrorClassification.INFRASTRUCTURE_FAILURE,
            ) from exc
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_thread = threading.Thread(
            target=_stream_redacted,
            args=(process.stdout, stdout),
            kwargs={"sensitive_values": sensitive_values},
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_stream_redacted,
            args=(process.stderr, stderr),
            kwargs={"sensitive_values": sensitive_values},
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        process.stdin.write(plan.bootstrap_prompt)
        process.stdin.close()
        try:
            exit_code = process.wait(timeout=plan.config.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(
                ("docker", "stop", "--time", "5", plan.container_name),
                capture_output=True,
                text=True,
                check=False,
            )
            exit_code = process.wait(timeout=30)
        stdout_thread.join(timeout=30)
        stderr_thread.join(timeout=30)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            raise RunnerFailure(
                "runner output stream did not close",
                ErrorClassification.INFRASTRUCTURE_FAILURE,
            )

    _redact_file(run_dir / "final.md", sensitive_values=sensitive_values)

    diagnostic_text = "\n".join(
        (
            errors_path.read_text(encoding="utf-8", errors="replace"),
            events_path.read_text(encoding="utf-8", errors="replace"),
        )
    )
    classification = classify_runner_result(exit_code, diagnostic_text, timed_out=timed_out)
    finished_at = utc_now()
    completion = {
        "type": "runner.completed",
        "timestamp": finished_at,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "error_classification": classification.value,
    }
    _append_event(events_path, completion)
    thread_id = _extract_thread_id(events_path)
    validation = dict(plan.run.validation_results)
    validation["runner"] = {
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "error_classification": classification.value,
        "command_metadata": diagnostic,
    }
    if classification is ErrorClassification.SUCCESS:
        status = plan.config.success_status
        next_action = plan.config.success_next_action
    elif classification is ErrorClassification.RATE_LIMITED:
        status = TaskStatus.WAITING_FOR_QUOTA
        next_action = "Preserve this run for a future quota-aware supervisor."
    elif classification in {
        ErrorClassification.SECURITY_VIOLATION,
        ErrorClassification.DATABASE_INTEGRITY_FAILURE,
    }:
        status = TaskStatus.BLOCKED
        next_action = "Stop for human investigation; never retry automatically."
    else:
        status = TaskStatus.FAILED
        next_action = "Preserve evidence for a future bounded supervisor or human review."
    updated_run = replace(
        plan.run,
        status=status,
        session_thread_id=thread_id or plan.run.session_thread_id,
        finished_at=finished_at,
        validation_results=validation,
        error_classification=classification,
        next_action=next_action,
    )
    save_run(run_dir / "state.json", updated_run)
    write_json_atomic(run_dir / "validation.json", validation)
    return completion


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-record", type=Path, required=True)
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--allowed-worktree-root", type=Path, required=True)
    parser.add_argument("--primary-runtime-worktree", type=Path, required=True)
    parser.add_argument("--prompt-path", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--auth-directory", type=Path, required=True)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort", choices=("minimal", "low", "medium", "high", "xhigh"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--env", action="append", default=[], dest="environment_names")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    task = load_task(args.task_record)
    run = load_run(args.run_record)
    config = RunnerConfig(
        allowed_worktree_root=args.allowed_worktree_root,
        primary_runtime_worktree=args.primary_runtime_worktree,
        prompt_path=args.prompt_path,
        run_directory=args.run_directory,
        auth_directory=args.auth_directory,
        image=args.image,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=args.timeout,
        environment_names=tuple(args.environment_names),
        dry_run=args.dry_run,
    )
    try:
        if config.dry_run:
            result = execute_launch(prepare_launch(task, run, config))
        else:
            result = run_with_quota_wait(
                args.task_record,
                args.run_record,
                lambda current_task, current_run: execute_launch(
                    prepare_launch(current_task, current_run, config)
                ),
            )
    except RunnerFailure as exc:
        print(json.dumps({"error": str(exc), "classification": exc.classification.value}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
