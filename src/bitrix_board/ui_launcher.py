from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from src.bitrix_board.agent_runner import is_process_alive
from src.bitrix_board.config import BoardConfig, load_config
from src.bitrix_board.prompts import build_plan_prompt
from src.bitrix_board.store import BoardStore, BoardTask


def _resolve_agent_command(config: BoardConfig) -> list[str]:
    from src.bitrix_board.agent_runner import _resolve_windows_agent_command

    command = [config.agent_bin, "create-chat"]
    if sys.platform.startswith("win"):
        command = _resolve_windows_agent_command(config, command)
    return command


def _resolve_agent_resume_command(
    config: BoardConfig,
    *,
    chat_id: str,
    worktree: Path,
) -> list[str]:
    from src.bitrix_board.agent_runner import _resolve_windows_agent_command

    command = [
        config.agent_bin,
        "--resume",
        chat_id,
        "--workspace",
        str(worktree),
        "--mode",
        "plan",
        "--trust",
        "--force",
        "-p",
        "--output-format",
        "text",
    ]
    if sys.platform.startswith("win"):
        command = _resolve_windows_agent_command(config, command)
    return command


def create_chat_id(config: BoardConfig) -> str:
    result = subprocess.run(
        _resolve_agent_command(config),
        cwd=config.repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "create-chat failed")
    chat_id = (result.stdout or "").strip().splitlines()[-1].strip()
    if not chat_id:
        raise RuntimeError("create-chat returned an empty chat id")
    return chat_id


def _resolve_cursor_bin() -> str:
    explicit = os.environ.get("CURSOR_BIN", "").strip()
    if explicit:
        return explicit
    for candidate in ("cursor", "cursor.cmd"):
        found = shutil.which(candidate)
        if found:
            return found
    local_programs = os.environ.get("LOCALAPPDATA", "")
    if local_programs:
        for relative in (
            r"Programs\cursor\Cursor.exe",
            r"Programs\cursor\resources\app\bin\cursor.cmd",
        ):
            path = Path(local_programs) / relative
            if path.exists():
                return str(path)
    return "cursor"


def open_cursor_window(worktree: Path) -> None:
    cursor_bin = _resolve_cursor_bin()
    subprocess.Popen(
        [cursor_bin, "-n", str(worktree)],
        cwd=worktree,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _plan_prompt_path(task: BoardTask) -> Path | None:
    if not task.worktree_path:
        return None
    candidate = Path(task.worktree_path) / "logs" / "planning.prompt.txt"
    return candidate if candidate.exists() else None


def _write_launch_manifest(
    config: BoardConfig,
    entries: list[dict[str, Any]],
) -> Path:
    manifest_path = config.repo_root / ".bitrix-board" / "ui-chats.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"launched_at": time.time(), "chats": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def stop_running_agents(store: BoardStore, run_id: int) -> None:
    for task in store.list_tasks(run_id):
        if task.agent_pid and is_process_alive(task.agent_pid):
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["taskkill", "/PID", str(task.agent_pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                )
            else:
                try:
                    os.kill(task.agent_pid, 15)
                except OSError:
                    pass
        store.update_task(task.id, agent_pid=None)


def launch_ui_chats(
    *,
    task_ids: list[int] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    config = load_config()
    store = BoardStore(config.db_path)
    launched: list[dict[str, Any]] = []
    try:
        run = store.get_latest_run()
        if run is None:
            raise RuntimeError("No board runs found. Start the dispatcher first.")

        stop_running_agents(store, run.id)
        store.request_stop(run.id)
        if run.dispatcher_pid and is_process_alive(run.dispatcher_pid):
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["taskkill", "/PID", str(run.dispatcher_pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                )
            else:
                try:
                    os.kill(run.dispatcher_pid, 15)
                except OSError:
                    pass

        candidates = [
            task
            for task in store.list_tasks(run.id)
            if task.worktree_path and task.state.value in {"planning", "preparing", "queued"}
        ]
        if task_ids:
            wanted = {int(task_id) for task_id in task_ids}
            candidates = [task for task in candidates if task.task_id in wanted]
        selected = candidates[:limit]
        if not selected:
            raise RuntimeError("No tasks with worktrees are available for UI launch.")

        for task in selected:
            worktree = Path(task.worktree_path or "")
            prompt_path = _plan_prompt_path(task)
            if prompt_path is None:
                raise RuntimeError(f"Planning prompt not found for task {task.task_id}")

            chat_id = create_chat_id(config)
            ui_title = f"Bitrix24 №{task.task_id}: {task.title}"
            open_cursor_window(worktree)

            prompt = prompt_path.read_text(encoding="utf-8")
            command = _resolve_agent_resume_command(
                config,
                chat_id=chat_id,
                worktree=worktree,
            )
            log_path = worktree / "logs" / "planning.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("w", encoding="utf-8")
            log_file.write(f"Chat ID: {chat_id}\n")
            log_file.write(f"UI title: {ui_title}\n")
            log_file.write(f"Command: {' '.join(command)}\n\n")
            log_file.flush()
            process = subprocess.Popen(
                command + [prompt],
                cwd=worktree,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            log_file.close()

            report = dict(task.report_json or {})
            report.update(
                {
                    "cursor_chat_id": chat_id,
                    "ui_title": ui_title,
                    "ui_launch_mode": "create-chat",
                }
            )
            store.update_task(
                task.id,
                agent_pid=process.pid,
                report_json=report,
            )

            launched.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "ui_title": ui_title,
                    "chat_id": chat_id,
                    "worktree": str(worktree),
                    "agent_pid": process.pid,
                    "prompt_path": str(prompt_path),
                }
            )

        manifest_path = _write_launch_manifest(config, launched)
        print(f"Launched {len(launched)} UI chats. Manifest: {manifest_path}")
        for entry in launched:
            print(
                f"- [{entry['task_id']}] {entry['ui_title']}\n"
                f"  chat: {entry['chat_id']}\n"
                f"  worktree: {entry['worktree']}"
            )
        return launched
    finally:
        store.close()
