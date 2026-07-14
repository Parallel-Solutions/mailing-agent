from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from src.bitrix_board.config import BoardConfig


def worktree_name(task_id: int) -> str:
    return f"bitrix-{task_id}"


def branch_name(task_id: int) -> str:
    return f"ai/bitrix-{task_id}"


def worktree_path(config: BoardConfig, task_id: int) -> Path:
    return config.worktrees_dir / worktree_name(task_id)


def ensure_worktree(config: BoardConfig, task_id: int) -> tuple[Path, str]:
    path = worktree_path(config, task_id)
    branch = branch_name(task_id)
    config.worktrees_dir.mkdir(parents=True, exist_ok=True)

    if path.exists() and (path / ".git").exists():
        _ensure_cursor_config(config, path)
        return path, branch

    if _branch_exists(config, branch):
        subprocess.run(
            ["git", "worktree", "add", str(path), branch],
            cwd=config.repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(path)],
            cwd=config.repo_root,
            check=True,
            capture_output=True,
            text=True,
        )

    _ensure_cursor_config(config, path)
    return path, branch


def _branch_exists(config: BoardConfig, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
        cwd=config.repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _ensure_cursor_config(config: BoardConfig, worktree: Path) -> None:
    source_mcp = config.repo_root / ".cursor" / "mcp.json"
    if not source_mcp.exists():
        return
    target_cursor = worktree / ".cursor"
    target_cursor.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_mcp, target_cursor / "mcp.json")


def task_state_path(worktree: Path) -> Path:
    state_dir = worktree / ".bitrix-board"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "task-state.json"


def task_log_dir(worktree: Path) -> Path:
    log_dir = worktree / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def save_task_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_task_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def git_diff_summary(worktree: Path, base_ref: str = "HEAD") -> str:
    result = subprocess.run(
        ["git", "diff", "--stat", base_ref],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    stat = result.stdout.strip()
    result_names = subprocess.run(
        ["git", "diff", "--name-only", base_ref],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    names = result_names.stdout.strip()
    parts = []
    if stat:
        parts.append(stat)
    if names:
        parts.append("Changed files:\n" + names)
    return "\n\n".join(parts) if parts else "(no changes)"
