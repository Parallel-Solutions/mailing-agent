from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

from src.bitrix_board.config import load_config
from src.bitrix_board.dispatcher import run_dispatcher
from src.bitrix_board.store import BoardStore


def _print_status() -> int:
    config = load_config()
    store = BoardStore(config.db_path)
    try:
        run = store.get_latest_run()
        if run is None:
            print("No board runs found.")
            return 0

        print(f"Run #{run.id}")
        print(f"Project: {run.project}")
        print(f"Source: {run.source_stage}")
        print(f"Target: {run.target_stage}")
        print(f"Concurrency: {run.concurrency}")
        print(f"Stop requested: {run.stop_requested}")
        print(f"Dispatcher PID: {run.dispatcher_pid or '-'}")
        print(f"Snapshot tasks: {run.snapshot_task_ids}")
        print()

        summary = store.status_summary(run.id)
        sections = [
            ("Active slots", summary["active_slots"]),
            ("Waiting for answer", summary["waiting_for_answer"]),
            ("Ready to resume", summary["ready_to_resume"]),
            ("Queue", summary["queue"]),
            ("Completed", summary["completed"]),
            ("Blocked", summary["blocked"]),
            ("Failed", summary["failed"]),
        ]
        for title, tasks in sections:
            print(f"## {title} ({len(tasks)})")
            if not tasks:
                print("  (empty)")
            for task in tasks:
                print(
                    f"  - [{task.task_id}] {task.title} | state={task.state.value}"
                    f" | branch={task.branch or '-'} | pid={task.agent_pid or '-'}"
                )
            print()
        return 0
    finally:
        store.close()


def _request_stop() -> int:
    config = load_config()
    store = BoardStore(config.db_path)
    try:
        run = store.get_latest_run()
        if run is None:
            print("No board runs found.")
            return 1
        store.request_stop(run.id)
        pid = run.dispatcher_pid
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                if sys.platform.startswith("win"):
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T"],
                        check=False,
                        capture_output=True,
                    )
        print(f"Stop requested for run #{run.id}")
        return 0
    finally:
        store.close()


def _resume_run() -> int:
    config = load_config()
    store = BoardStore(config.db_path)
    try:
        run = store.get_latest_run()
        if run is None:
            print("No board runs found.")
            return 1
        if run.dispatcher_pid and _pid_alive(run.dispatcher_pid):
            print(f"Run #{run.id} is already running (PID {run.dispatcher_pid}).")
            return 1
        store.clear_stop(run.id)
        return run_dispatcher(
            project=run.project,
            source_stage=run.source_stage,
            target_stage=run.target_stage,
            concurrency=run.concurrency,
            resume_run_id=run.id,
        )
    finally:
        store.close()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _interactive_params() -> tuple[str, str, str, int]:
    project = input("Bitrix24 project name: ").strip()
    source_stage = input("Source stage/column: ").strip()
    target_stage = input("Target stage/column: ").strip()
    concurrency_raw = input("Concurrency [3]: ").strip() or "3"
    concurrency = max(1, int(concurrency_raw))
    return project, source_stage, target_stage, concurrency


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bitrix24 board dispatcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start processing a board snapshot")
    start_parser.add_argument("--project", default="")
    start_parser.add_argument("--source-stage", default="")
    start_parser.add_argument("--target-stage", default="")
    start_parser.add_argument("--concurrency", type=int, default=3)
    start_parser.add_argument("--interactive", action="store_true")

    subparsers.add_parser("status", help="Show dispatcher status")
    subparsers.add_parser("stop", help="Request graceful stop")
    subparsers.add_parser("resume", help="Resume the latest run")

    args = parser.parse_args(argv)

    if args.command == "status":
        return _print_status()
    if args.command == "stop":
        return _request_stop()
    if args.command == "resume":
        return _resume_run()

    project = args.project
    source_stage = args.source_stage
    target_stage = args.target_stage
    concurrency = args.concurrency

    if args.interactive or not (project and source_stage and target_stage):
        project, source_stage, target_stage, concurrency = _interactive_params()

    if not project or not source_stage or not target_stage:
        print("Project, source stage and target stage are required.", file=sys.stderr)
        return 1

    return run_dispatcher(
        project=project,
        source_stage=source_stage,
        target_stage=target_stage,
        concurrency=concurrency,
    )


if __name__ == "__main__":
    raise SystemExit(main())
