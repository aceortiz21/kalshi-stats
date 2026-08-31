"""User entry point for the durable Automation V1 queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Sequence

from .automation_dispatcher import Dispatcher
from .automation_state import TaskPriority, TaskRecord, load_run, load_task, save_task


def _root() -> Path:
    return Path.cwd().resolve()


def _task_id(title: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", title.strip().lower()).strip("-") or "task"
    base = value[:48]
    candidate = base
    index = 2
    while (_root() / "automation" / "tasks" / f"{candidate}.json").exists():
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _dispatcher(args: argparse.Namespace) -> Dispatcher:
    root = _root()
    return Dispatcher(
        repository=root,
        allowed_worktree_root=Path(args.worktree_root).resolve() if args.worktree_root else root.parent / "tasks",
        primary_runtime_worktree=Path(args.primary_runtime_worktree).resolve() if args.primary_runtime_worktree else Path.home() / "stats",
        base_branch=args.base_branch,
        auth_directory=Path(args.auth_directory).resolve() if args.auth_directory else root.parent / "automation-codex-home",
        image=args.image,
        timeout_seconds=args.timeout,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit", help="submit a bounded task specification")
    submit.add_argument("--title", required=True)
    submit.add_argument("--spec", required=True, type=Path)
    submit.add_argument("--priority", choices=[p.value for p in TaskPriority], default=TaskPriority.NORMAL.value)
    submit.add_argument("--depends-on", action="append", default=[])
    submit.add_argument("--base-branch", default="automation/phase-c2b-v1")
    submit.add_argument("--max-attempts", type=int, default=3)
    submit.add_argument("--task-id")

    list_parser = sub.add_parser("list", help="list durable tasks")
    list_parser.add_argument("--json", action="store_true")
    status = sub.add_parser("status", help="show task and run state")
    status.add_argument("task_id")
    run = sub.add_parser("run", help="run the single-worker dispatcher")
    run.add_argument("--once", action="store_true")
    run.add_argument("--continuous", action="store_true")
    run.add_argument("--worktree-root", type=Path)
    run.add_argument("--primary-runtime-worktree", type=Path)
    run.add_argument("--auth-directory", type=Path)
    run.add_argument("--base-branch", default="automation/phase-c2b-v1")
    run.add_argument("--image", default="kalshi-stats-automation:phase-b-v1")
    run.add_argument("--timeout", type=int, default=7200)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = _root()
    task_dir = root / "automation" / "tasks"
    if args.command == "submit":
        spec = args.spec.resolve(strict=True)
        if not spec.is_file() or not spec.is_relative_to(root):
            raise SystemExit("task specification must be a file inside the repository")
        task_id = args.task_id or _task_id(args.title)
        branch = f"automation/{task_id}"
        task = TaskRecord.create(
            task_id=task_id, title=args.title, objective=spec.read_text(encoding="utf-8").strip(),
            branch=branch, worktree=str((root.parent / "tasks" / task_id).resolve()),
            dependencies=tuple(args.depends_on), max_attempts=args.max_attempts,
            priority=args.priority, prompt_path=str(spec.relative_to(root)), base_branch=args.base_branch,
        )
        save_task(task_dir / f"{task_id}.json", task)
        print(json.dumps(task.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "list":
        values = [load_task(path).to_dict() for path in sorted(task_dir.glob("*.json"))]
        if args.json:
            print(json.dumps(values, indent=2, sort_keys=True))
        else:
            for value in values:
                print(f"{value['task_id']}\t{value['priority']}\t{value['status']}\t{value['title']}\t{value.get('current_run_id') or '-'}")
        return 0
    if args.command == "status":
        task = load_task(task_dir / f"{args.task_id}.json")
        result = {"task": task.to_dict(), "runs": []}
        worktree = Path(task.worktree)
        for run_id in task.run_ids:
            path = worktree / "automation" / "runs" / run_id / "state.json"
            if path.exists():
                result["runs"].append(load_run(path).to_dict())
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    processed = _dispatcher(args).run(once=args.once, continuous=args.continuous)
    print(json.dumps([task.to_dict() for task in processed], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
