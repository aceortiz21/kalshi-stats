"""Host bootstrap CLI for the Automation V1 Telegram controller."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .telegram_controller import (
    AutomationController, ControllerPaths, OwnedProcess, build_worker_command,
    load_credentials,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "recover", "controller-run"))
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--data-root", type=Path, default=Path.home() / ".local/share/kalshi-stats-automation")
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--worktree-root", type=Path)
    parser.add_argument("--primary-runtime", type=Path, default=Path.home() / "stats")
    parser.add_argument("--auth-directory", type=Path)
    parser.add_argument("--image", default="kalshi-stats-automation:phase-b-v1")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    repository = args.repository.resolve()
    paths = ControllerPaths(args.data_root.resolve())
    controller_process = OwnedProcess(paths.controller_pid, "kalshi_stats.telegram_controller_cli")
    if args.action == "status":
        worker = OwnedProcess(paths.worker_pid, "kalshi_stats.automation_cli")
        print(f"Telegram controller: {'RUNNING' if controller_process.running() else 'STOPPED'}")
        print(f"Automation worker: {'RUNNING' if worker.running() else 'STOPPED'}")
        print(f"State/log root: {paths.runtime}")
        return 0
    if args.action == "stop":
        print(f"Telegram controller: {controller_process.stop()}")
        return 0
    credentials_path = args.credentials or paths.data_root / "telegram.env"
    credentials = load_credentials(credentials_path)
    worktree_root = (args.worktree_root or repository.parent / "tasks").resolve()
    auth_directory = (args.auth_directory or paths.data_root / "codex-home").resolve()
    worker_command = build_worker_command(
        repository, worktree_root=worktree_root, primary_runtime=args.primary_runtime.resolve(),
        auth_directory=auth_directory, image=args.image,
    )
    controller = AutomationController(
        repository=repository, paths=paths, credentials=credentials,
        worker_command=worker_command,
    )
    if args.action == "recover":
        print(controller.recover())
        return 0
    if args.action == "controller-run":
        controller.run()
        return 0
    command = (
        sys.executable, "-m", "kalshi_stats.telegram_controller_cli", "controller-run",
        "--repository", str(repository), "--data-root", str(paths.data_root),
        "--credentials", str(credentials_path), "--worktree-root", str(worktree_root),
        "--primary-runtime", str(args.primary_runtime.resolve()),
        "--auth-directory", str(auth_directory), "--image", args.image,
    )
    print(f"Telegram controller: {controller_process.start(command, log_path=paths.log)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
