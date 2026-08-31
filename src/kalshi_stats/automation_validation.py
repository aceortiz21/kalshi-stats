"""Reusable, fail-closed mechanical validation for autonomous task worktrees."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Iterable, Sequence

from .automation_runner import redact_diagnostic_text
from .automation_state import TaskRecord, is_valid_task_branch, utc_now, write_json_atomic


PROTECTED_PREFIXES = ("data/", "reports/")
PROTECTED_SUFFIXES = (".sqlite", ".sqlite3", ".db")
FORBIDDEN_LIVE_PATHS = (Path.home() / "stats",)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(?:OPENAI_API_KEY|KALSHI_API_KEY_ID|KALSHI_PRIVATE_KEY|KALSHI_PRIVATE_KEY_PATH)\s*[:=]\s*[^\s]+"),
)


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    required: bool = True
    exit_code: int | None = None
    detail: str = ""
    classification: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationReport:
    started_at: str
    finished_at: str
    passed: bool
    changed_files: tuple[str, ...]
    gates: tuple[GateResult, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "passed": self.passed,
            "changed_files": list(self.changed_files),
            "gates": [gate.to_dict() for gate in self.gates],
        }


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    configured_worktree = environment.get("GIT_WORK_TREE")
    if not configured_worktree or Path(configured_worktree).resolve() != root.resolve():
        environment.pop("GIT_DIR", None)
        environment.pop("GIT_WORK_TREE", None)
    return subprocess.run(
        ("git", "-C", str(root), *arguments), capture_output=True, text=True,
        check=False, env=environment,
    )


def changed_files(root: Path, base_ref: str) -> tuple[str, ...]:
    committed = _git(root, "diff", "--name-only", "--diff-filter=ACMRTUXB", f"{base_ref}...HEAD")
    working = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if committed.returncode:
        raise RuntimeError("unable to determine changed files from the configured base")
    names = {line for line in committed.stdout.splitlines() if line}
    for line in working.stdout.splitlines():
        if len(line) >= 4:
            value = line[3:].split(" -> ")[-1]
            names.add(value)
    return tuple(sorted(names))


def worktree_fingerprint(root: Path, *, excluded_prefixes: Sequence[str] = ()) -> str:
    """Hash tracked/untracked task content so reviewer edits cannot hide."""

    listing = _git(root, "ls-files", "-co", "--exclude-standard", "-z")
    if listing.returncode:
        raise RuntimeError("unable to fingerprint task worktree")
    digest = hashlib.sha256()
    for name in sorted(filter(None, listing.stdout.split("\0"))):
        if any(name == prefix.rstrip("/") or name.startswith(prefix) for prefix in excluded_prefixes):
            continue
        path = root / name
        if not path.is_file():
            continue
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def worktree_snapshot(root: Path) -> dict[str, str]:
    """Hash repository files, including ignored evidence, without protected hosts."""

    names: set[str] = set()
    for arguments in (
        ("ls-files", "-co", "--exclude-standard", "-z"),
        ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
    ):
        result = _git(root, *arguments)
        if result.returncode:
            raise RuntimeError("unable to snapshot task worktree")
        names.update(filter(None, result.stdout.split("\0")))
    snapshot: dict[str, str] = {}
    for name in sorted(names):
        if name.startswith((".venv/", "__pycache__/")) or "/__pycache__/" in name:
            continue
        path = root / name
        if path.is_file():
            snapshot[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _command_gate(root: Path, name: str, command: Sequence[str]) -> GateResult:
    environment = dict(os.environ)
    configured_worktree = environment.get("GIT_WORK_TREE")
    if not configured_worktree or Path(configured_worktree).resolve() != root.resolve():
        environment.pop("GIT_DIR", None)
        environment.pop("GIT_WORK_TREE", None)
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True, check=False,
        env=environment,
    )
    detail = redact_diagnostic_text((result.stdout + "\n" + result.stderr).strip())
    return GateResult(name, result.returncode == 0, exit_code=result.returncode, detail=detail[-4000:])


def _parse_gate(root: Path, files: Iterable[str], suffix: str, parser) -> GateResult:
    selected = [name for name in files if name.endswith(suffix) and (root / name).is_file()]
    try:
        for name in selected:
            with (root / name).open("rb" if suffix == ".toml" else "r", encoding=None if suffix == ".toml" else "utf-8") as stream:
                parser(stream)
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        return GateResult(f"parse_{suffix.removeprefix('.')}", False, detail=f"{name}: {type(exc).__name__}")
    return GateResult(f"parse_{suffix.removeprefix('.')}", True, detail=f"parsed {len(selected)} file(s)")


def _secret_gate(root: Path, files: Iterable[str]) -> GateResult:
    matches: list[str] = []
    for name in files:
        path = root / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            matches.append(name)
    return GateResult(
        "secret_scan", not matches,
        detail="no likely secrets in changed files" if not matches else f"secret-shaped material detected in: {', '.join(matches)}",
        classification=None if not matches else "SECURITY_VIOLATION",
    )


def run_mechanical_validation(
    task: TaskRecord,
    *,
    worktree: Path,
    output_path: Path,
    base_ref: str | None = None,
) -> ValidationReport:
    """Run the ordinary Python-development gate set without touching live data."""

    started = utc_now()
    root = worktree.resolve(strict=True)
    files = changed_files(root, base_ref or task.base_sha or task.base_branch)
    gates: list[GateResult] = []
    safe_identity = (
        is_valid_task_branch(task.branch)
        and task.branch not in {"main", "automation-integration"}
        and root != (Path.home() / "stats").resolve(strict=False)
        and root == Path(task.worktree).resolve(strict=False)
    )
    gates.append(GateResult("task_safety", safe_identity, classification=None if safe_identity else "SECURITY_VIOLATION"))
    protected = [name for name in files if name.startswith(PROTECTED_PREFIXES) or name.endswith(PROTECTED_SUFFIXES)]
    gates.append(GateResult(
        "protected_evidence", not protected,
        detail="no protected database/evidence artifacts changed" if not protected else f"protected artifacts changed: {', '.join(protected)}",
        classification=None if not protected else "DATABASE_INTEGRITY_FAILURE",
    ))
    forbidden = [name for name in files if name.startswith((".env", ".ssh/", ".aws/", ".config/", ".codex/"))]
    gates.append(GateResult(
        "changed_file_policy", not forbidden,
        detail="changed files obey policy" if not forbidden else f"forbidden paths changed: {', '.join(forbidden)}",
        classification=None if not forbidden else "SECURITY_VIOLATION",
    ))
    gates.append(_secret_gate(root, files))
    project_python = root / ".venv" / "bin" / "python"
    python = project_python if project_python.is_file() else Path(sys.executable)
    gates.append(_command_gate(root, "compileall", (str(python), "-m", "compileall", "-q", "src", "tests")))
    gates.append(_command_gate(root, "pytest", (str(python), "-m", "pytest", "-q")))
    gates.append(_command_gate(root, "git_diff_check", ("git", "diff", "--check")))
    shell_files = sorted({name for name in files if name.endswith(".sh")} | {name for name in ("start.sh", "snapshot.sh") if (root / name).is_file()})
    for name in shell_files:
        gates.append(_command_gate(root, f"shell_syntax:{name}", ("bash", "-n", name)))
    json_files = set(name for name in files if name.endswith(".json"))
    json_files.update(str(path.relative_to(root)) for path in (root / "automation" / "schemas").glob("*.json"))
    gates.append(_parse_gate(root, sorted(json_files), ".json", json.load))
    gates.append(_parse_gate(root, files, ".toml", tomllib.load))
    passed = all(not gate.required or gate.passed for gate in gates)
    report = ValidationReport(started, utc_now(), passed, files, tuple(gates))
    write_json_atomic(output_path, report.to_dict())
    return report
