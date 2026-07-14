from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.bitrix_board.config import BoardConfig

AI_QUESTION_PREFIX = "ИИ приостановил планирование задачи"
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
JSON_OBJECT_RE = re.compile(
    r"(\{[^{}]*\"status\"\s*:\s*\"(?:ready|blocked|done|failed|approved|changes_requested)\"[^{}]*\})",
    re.DOTALL,
)


@dataclass
class AgentResult:
    exit_code: int
    stdout: str
    stderr: str
    log_path: Path
    parsed: dict[str, Any] | None


@dataclass
class SpawnedAgent:
    pid: int
    log_path: Path
    phase: str


def parse_agent_json(stdout: str) -> dict[str, Any] | None:
    for pattern in (JSON_BLOCK_RE, JSON_OBJECT_RE):
        for match in pattern.finditer(stdout):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and "status" in payload:
                return payload

    start = stdout.rfind("{")
    while start >= 0:
        end = stdout.find("}", start)
        while end >= 0:
            chunk = stdout[start : end + 1]
            try:
                payload = json.loads(chunk)
            except json.JSONDecodeError:
                end = stdout.find("}", end + 1)
                continue
            if isinstance(payload, dict) and "status" in payload:
                return payload
            end = stdout.find("}", end + 1)
        start = stdout.rfind("{", 0, start)
    return None


def _build_command(
    config: BoardConfig,
    *,
    prompt: str,
    mode: str | None = None,
    force: bool = False,
) -> list[str]:
    command = [config.agent_bin, "-p", prompt]
    if mode:
        command.extend(["--mode", mode])
    if force:
        command.append("--force")
    command.extend(["--output-format", "text"])
    return command


def _prepare_log_paths(worktree: Path, phase: str) -> tuple[Path, Path]:
    log_dir = worktree / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{phase}.log"
    prompt_path = log_dir / f"{phase}.prompt.txt"
    return log_path, prompt_path


def spawn_agent(
    config: BoardConfig,
    *,
    worktree: Path,
    prompt: str,
    phase: str,
    mode: str | None = None,
    force: bool = False,
) -> SpawnedAgent:
    log_path, prompt_path = _prepare_log_paths(worktree, phase)
    prompt_path.write_text(prompt, encoding="utf-8")
    command = _build_command(config, prompt=prompt, mode=mode, force=force)

    log_file = log_path.open("w", encoding="utf-8")
    log_file.write(f"Command: {' '.join(command)}\n")
    log_file.write(f"Worktree: {worktree}\n\n")
    log_file.flush()

    process = subprocess.Popen(
        command,
        cwd=worktree,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_file.close()
    return SpawnedAgent(pid=process.pid, log_path=log_path, phase=phase)


def load_agent_result(log_path: Path) -> AgentResult:
    content = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    exit_code = 1
    stdout = content
    stderr = ""
    if "=== EXIT CODE:" in content:
        parts = content.split("=== EXIT CODE:")
        stdout = parts[0]
        try:
            exit_code = int(parts[-1].strip().splitlines()[0])
        except (ValueError, IndexError):
            exit_code = 1
    else:
        parsed = parse_agent_json(content)
        if parsed and str(parsed.get("status", "")).lower() in {
            "ready",
            "done",
            "approved",
            "blocked",
            "changes_requested",
        }:
            exit_code = 0
    parsed = parse_agent_json(stdout)
    return AgentResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        log_path=log_path,
        parsed=parsed,
    )


def run_agent(
    config: BoardConfig,
    *,
    worktree: Path,
    prompt: str,
    phase: str,
    mode: str | None = None,
    force: bool = False,
) -> AgentResult:
    log_path, prompt_path = _prepare_log_paths(worktree, phase)
    prompt_path.write_text(prompt, encoding="utf-8")
    command = _build_command(config, prompt=prompt, mode=mode, force=force)

    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"Command: {' '.join(command)}\n")
        log_file.write(f"Worktree: {worktree}\n\n")
        log_file.flush()
        process = subprocess.run(
            command,
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        log_file.write("=== STDOUT ===\n")
        log_file.write(process.stdout or "")
        log_file.write("\n=== STDERR ===\n")
        log_file.write(process.stderr or "")
        log_file.write(f"\n=== EXIT CODE: {process.returncode} ===\n")

    parsed = parse_agent_json(process.stdout or "")
    return AgentResult(
        exit_code=process.returncode,
        stdout=process.stdout or "",
        stderr=process.stderr or "",
        log_path=log_path,
        parsed=parsed,
    )


def is_process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                check=False,
            )
            return str(pid) in result.stdout
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def format_questions_message(questions: list[str]) -> str:
    lines = [
        AI_QUESTION_PREFIX,
        "",
        "Для продолжения необходимо уточнить:",
        "",
    ]
    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. {question}")
    lines.extend(
        [
            "",
            "Ответьте в чате этой задачи. После ответа работа продолжится автоматически.",
        ]
    )
    return "\n".join(lines)
