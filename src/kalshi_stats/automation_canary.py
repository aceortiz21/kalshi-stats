"""Authenticated zero-approval canary using the real Automation V1 path."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping, Sequence

from .automation_runner import (
    RUN_FILENAMES,
    RunnerConfig,
    execute_launch,
    prepare_launch,
)
from .automation_state import (
    ErrorClassification,
    RunRecord,
    TaskRecord,
    TaskStatus,
    save_run,
    save_task,
    transition_task,
    utc_now,
    write_json_atomic,
)
from .automation_worktrees import cleanup_task_worktree, create_task_worktree


CANARY_TEMPLATE = Path("automation/canary/PROMPT.md")
CANARY_EXPECTED_LINE = "AUTOMATION_PHASE_C1_CANARY_OK"


@dataclass(frozen=True)
class CanaryDefinition:
    task_id: str
    run_id: str
    branch: str
    base_branch: str
    worktree: Path

    @property
    def target(self) -> Path:
        return Path("automation") / "runs" / self.run_id / "canary-output.txt"


@dataclass(frozen=True)
class PreparedCanary:
    definition: CanaryDefinition
    task: TaskRecord
    run: RunRecord
    prompt_path: Path
    run_directory: Path
    prompt_sha256: str


def render_canary_prompt(template: str, definition: CanaryDefinition) -> str:
    rendered = template.replace("{{CANARY_TARGET}}", definition.target.as_posix())
    rendered = rendered.replace("{{EXPECTED_LINE}}", CANARY_EXPECTED_LINE)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("canary prompt contains an unresolved template placeholder")
    return rendered


def create_canary_task(
    definition: CanaryDefinition,
    *,
    repository: Path,
    allowed_worktree_root: Path,
    allowed_base_branches: Sequence[str],
    primary_runtime_worktree: Path,
) -> PreparedCanary:
    """Create TaskRecord -> branch/worktree -> RunRecord -> run inputs."""

    created_at = utc_now()
    task = TaskRecord.create(
        task_id=definition.task_id,
        title="Authenticated zero-approval Automation V1 canary",
        objective=(
            f"Create only {definition.target.as_posix()} containing exactly "
            f"{CANARY_EXPECTED_LINE!r} followed by one newline."
        ),
        branch=definition.branch,
        worktree=str(definition.worktree.resolve(strict=False)),
        max_attempts=1,
        next_action="Execute the frozen canary prompt once through the Phase B runner.",
        now=created_at,
    )
    create_task_worktree(
        repository=repository,
        base_branch=definition.base_branch,
        task_branch=definition.branch,
        worktree_path=definition.worktree,
        allowed_worktree_root=allowed_worktree_root,
        allowed_base_branches=allowed_base_branches,
        primary_runtime_worktree=primary_runtime_worktree,
    )

    worktree = definition.worktree.resolve(strict=True)
    template = (repository / CANARY_TEMPLATE).read_text(encoding="utf-8")
    prompt = render_canary_prompt(template, definition)
    prompt_path = worktree / "automation" / "tasks" / f"{definition.task_id}.prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    task = replace(
        task,
        run_ids=(definition.run_id,),
        report_paths=(f"automation/runs/{definition.run_id}",),
    )
    task = transition_task(
        task,
        TaskStatus.RUNNING,
        next_action="Execute the frozen canary prompt once through the Phase B runner.",
    )
    task_path = worktree / "automation" / "tasks" / f"{definition.task_id}.json"
    save_task(task_path, task)

    run_directory = worktree / "automation" / "runs" / definition.run_id
    run = RunRecord(
        run_id=definition.run_id,
        task_id=definition.task_id,
        status=TaskStatus.RUNNING,
        session_thread_id=None,
        started_at=utc_now(),
        finished_at=None,
        branch=definition.branch,
        worktree=str(worktree),
        files_changed=(),
        validation_results={
            "canary_definition": {
                "prompt_sha256": prompt_sha256,
                "target": definition.target.as_posix(),
                "expected_line": CANARY_EXPECTED_LINE,
                "approval_policy": "never",
                "expected_human_approvals": 0,
            }
        },
        final_response_path=f"automation/runs/{definition.run_id}/final.md",
        jsonl_log_path=f"automation/runs/{definition.run_id}/events.jsonl",
        error_classification=None,
        next_action="Execute the frozen canary prompt once through the Phase B runner.",
    )
    return PreparedCanary(
        definition=definition,
        task=task,
        run=run,
        prompt_path=prompt_path,
        run_directory=run_directory,
        prompt_sha256=prompt_sha256,
    )


def _approval_request_count(events_path: Path) -> int:
    count = 0
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", "")).lower()
        if "approval" in event_type and any(
            marker in event_type for marker in ("request", "required", "prompt")
        ):
            count += 1
    return count


def validate_canary_success(prepared: PreparedCanary) -> dict[str, Any]:
    run_dir = prepared.run_directory
    missing = [name for name in RUN_FILENAMES if not (run_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"canary evidence files are missing: {', '.join(missing)}")
    target = prepared.definition.worktree / prepared.definition.target
    if not target.is_file() or target.read_text(encoding="utf-8") != f"{CANARY_EXPECTED_LINE}\n":
        raise RuntimeError("canary expected output is missing or incorrect")
    events = run_dir / "events.jsonl"
    final = run_dir / "final.md"
    if not events.read_text(encoding="utf-8").strip():
        raise RuntimeError("canary events.jsonl is absent or empty")
    if not final.read_text(encoding="utf-8").strip():
        raise RuntimeError("canary final.md is absent or empty")

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    runner = state.get("validation_results", {}).get("runner", {})
    command = runner.get("command_metadata", {}).get("codex_command", [])
    if runner.get("exit_code") != 0 or runner.get("error_classification") != "SUCCESS":
        raise RuntimeError("canary runner did not record a successful zero exit")
    try:
        approval_policy = command[command.index("--ask-for-approval") + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError("canary command did not record an approval policy") from exc
    approval_requests = _approval_request_count(events)
    if approval_policy != "never" or approval_requests != 0:
        raise RuntimeError("canary zero-approval success criterion failed")
    mounts = runner.get("command_metadata", {}).get("mounts", [])
    if len(mounts) != 2 or mounts[1].get("source") != "<dedicated-automation-auth>":
        raise RuntimeError("canary mount evidence is absent or not redacted")

    return {
        "status": "PASSED",
        "expected_output": definition_target(prepared),
        "prompt_sha256": prepared.prompt_sha256,
        "human_command_approvals": 0,
        "approval_requests_in_events": approval_requests,
        "exit_code": runner["exit_code"],
        "events_present": True,
        "final_present": True,
        "session_thread_id": state.get("session_thread_id"),
        "mount_count": len(mounts),
        "only_intentional_credential_mount": "/codex-home",
    }


def definition_target(prepared: PreparedCanary) -> str:
    return prepared.definition.target.as_posix()


def _git_readonly(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _boundary_snapshot(repository: Path, primary_runtime_worktree: Path) -> dict[str, Any]:
    process = subprocess.run(
        ("pgrep", "-fc", "kalshi_stats"),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "main_sha": _git_readonly(repository, "rev-parse", "refs/heads/main"),
        "runtime_branch": _git_readonly(primary_runtime_worktree, "branch", "--show-current"),
        "runtime_git_status": _git_readonly(primary_runtime_worktree, "status", "--short").splitlines(),
        "relevant_runtime_process_count": int(process.stdout.strip() or "0"),
    }


def _archive_evidence(prepared: PreparedCanary, repository: Path) -> Path:
    archive = repository / "automation" / "runs" / prepared.definition.run_id
    if archive.exists():
        raise RuntimeError("canary evidence archive already exists")
    shutil.copytree(prepared.run_directory, archive)
    shutil.copy2(prepared.prompt_path, archive / "canary-prompt.md")
    task_source = (
        prepared.definition.worktree
        / "automation"
        / "tasks"
        / f"{prepared.definition.task_id}.json"
    )
    shutil.copy2(task_source, repository / "automation" / "tasks" / task_source.name)
    return archive


def run_authenticated_canary(
    prepared: PreparedCanary,
    *,
    repository: Path,
    allowed_worktree_root: Path,
    allowed_base_branches: Sequence[str],
    primary_runtime_worktree: Path,
    auth_directory: Path,
    image: str,
    timeout_seconds: int,
) -> Mapping[str, Any]:
    boundary_before = _boundary_snapshot(repository, primary_runtime_worktree)
    initial_results = dict(prepared.run.validation_results)
    initial_results["boundary_before"] = boundary_before
    run = replace(prepared.run, validation_results=initial_results)
    config = RunnerConfig(
        allowed_worktree_root=allowed_worktree_root,
        primary_runtime_worktree=primary_runtime_worktree,
        prompt_path=prepared.prompt_path,
        run_directory=prepared.run_directory,
        auth_directory=auth_directory,
        image=image,
        timeout_seconds=timeout_seconds,
    )
    plan = prepare_launch(prepared.task, run, config)
    completion = execute_launch(plan)
    boundary_after = _boundary_snapshot(repository, primary_runtime_worktree)
    state_path = prepared.run_directory / "state.json"
    try:
        validation = validate_canary_success(prepared)
    except RuntimeError as exc:
        failed_state = RunRecord.from_dict(json.loads(state_path.read_text(encoding="utf-8")))
        failed_results = dict(failed_state.validation_results)
        failed_results["canary"] = {
            "status": "FAILED",
            "reason": str(exc),
            "human_command_approvals": _approval_request_count(
                prepared.run_directory / "events.jsonl"
            ),
        }
        failed_results["boundary_before"] = boundary_before
        failed_results["boundary_after"] = boundary_after
        failed_state = replace(
            failed_state,
            status=TaskStatus.FAILED,
            validation_results=failed_results,
            next_action="Preserve the failed canary for diagnosis; do not retry automatically.",
        )
        save_run(state_path, failed_state)
        write_json_atomic(prepared.run_directory / "validation.json", failed_results)
        failed_task = transition_task(
            prepared.task,
            TaskStatus.FAILED,
            next_action="Preserve the failed canary for human review.",
            last_error=str(exc),
        )
        save_task(
            prepared.definition.worktree
            / "automation"
            / "tasks"
            / f"{prepared.definition.task_id}.json",
            failed_task,
        )
        _archive_evidence(prepared, repository)
        raise
    for stable_field in ("main_sha", "runtime_branch", "runtime_git_status"):
        if boundary_after[stable_field] != boundary_before[stable_field]:
            raise RuntimeError(f"canary changed protected boundary field: {stable_field}")
    credential_candidates = [
        path
        for pattern in (".env", ".env.*", "*.pem", "*.key")
        for path in prepared.definition.worktree.rglob(pattern)
    ]
    if credential_candidates:
        raise RuntimeError("canary worktree contains a live credential candidate")
    validation["boundary_before"] = boundary_before
    validation["boundary_after"] = boundary_after
    validation["main_untouched"] = True
    validation["primary_runtime_git_tree_untouched"] = True
    validation["live_money_credential_available"] = False
    validation["other_host_secret_mounts"] = 0

    state = RunRecord.from_dict(json.loads(state_path.read_text(encoding="utf-8")))
    results = dict(state.validation_results)
    results["canary"] = validation
    state = replace(
        state,
        status=TaskStatus.PASSED,
        files_changed=(prepared.definition.target.as_posix(),),
        validation_results=results,
        error_classification=ErrorClassification.SUCCESS,
        next_action="Canary passed; preserve evidence and remove the disposable worktree.",
    )
    save_run(state_path, state)
    write_json_atomic(prepared.run_directory / "validation.json", results)

    task = transition_task(
        prepared.task,
        TaskStatus.VALIDATING,
        next_action="Validate the exact canary output and zero-approval evidence.",
    )
    task = transition_task(task, TaskStatus.REVIEWING, next_action="Record canary result.")
    task = transition_task(task, TaskStatus.PASSED, next_action="Archive canary evidence.")
    task_path = (
        prepared.definition.worktree
        / "automation"
        / "tasks"
        / f"{prepared.definition.task_id}.json"
    )
    save_task(task_path, task)
    archive = _archive_evidence(prepared, repository)

    cleanup_task_worktree(
        repository=repository,
        base_branch=prepared.definition.base_branch,
        task_branch=prepared.definition.branch,
        worktree_path=prepared.definition.worktree,
        allowed_worktree_root=allowed_worktree_root,
        allowed_base_branches=allowed_base_branches,
        primary_runtime_worktree=primary_runtime_worktree,
        disposable_paths=(
            Path("automation") / "tasks" / f"{prepared.definition.task_id}.json",
            Path("automation") / "tasks" / f"{prepared.definition.task_id}.prompt.md",
            Path("automation") / "runs" / prepared.definition.run_id,
        ),
    )
    return {
        "completion": completion,
        "validation": validation,
        "evidence_directory": str(archive.relative_to(repository)),
        "task_record": str(
            (repository / "automation" / "tasks" / f"{prepared.definition.task_id}.json")
            .relative_to(repository)
        ),
        "worktree_cleaned": not prepared.definition.worktree.exists(),
        "branch_cleaned": True,
        "codex_command": plan.diagnostic()["codex_command"],
        "docker_command": plan.diagnostic()["docker_command"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--task-branch", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--allowed-worktree-root", type=Path, required=True)
    parser.add_argument("--primary-runtime-worktree", type=Path, required=True)
    parser.add_argument("--auth-directory", type=Path, required=True)
    parser.add_argument("--image", default="kalshi-stats-automation:phase-b-v1")
    parser.add_argument("--timeout", type=int, default=900)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    definition = CanaryDefinition(
        task_id=args.task_id,
        run_id=args.run_id,
        branch=args.task_branch,
        base_branch=args.base_branch,
        worktree=args.worktree,
    )
    prepared = create_canary_task(
        definition,
        repository=args.repository,
        allowed_worktree_root=args.allowed_worktree_root,
        allowed_base_branches=(args.base_branch,),
        primary_runtime_worktree=args.primary_runtime_worktree,
    )
    result = run_authenticated_canary(
        prepared,
        repository=args.repository,
        allowed_worktree_root=args.allowed_worktree_root,
        allowed_base_branches=(args.base_branch,),
        primary_runtime_worktree=args.primary_runtime_worktree,
        auth_directory=args.auth_directory,
        image=args.image,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
