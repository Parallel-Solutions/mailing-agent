from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.bitrix_board.config import BoardConfig
from src.bitrix_board.worktree import branch_name

AI_QUESTION_PREFIX = "ИИ приостановил планирование задачи"
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
JSON_OBJECT_RE = re.compile(
    r"(\{[^{}]*\"status\"\s*:\s*\"(?:ready|blocked|done|failed|approved|changes_requested)\"[^{}]*\})",
    re.DOTALL,
)
ACTIVE_META_STATUSES = frozenset({"starting", "running"})


@dataclass
class AgentResult:
    exit_code: int
    stdout: str
    stderr: str
    log_path: Path
    parsed: dict[str, Any] | None


@dataclass
class SpawnedAgent:
    pid: int | None
    log_path: Path
    phase: str
    meta_path: Path | None = None
    cursor_agent_id: str | None = None


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


def _prepare_log_paths(worktree: Path, phase: str) -> tuple[Path, Path, Path]:
    log_dir = worktree / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    meta_dir = worktree / ".bitrix-board"
    meta_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{phase}.log"
    prompt_path = log_dir / f"{phase}.prompt.txt"
    meta_path = meta_dir / f"{phase}.agent.json"
    return log_path, prompt_path, meta_path


def _write_meta(meta_path: Path, payload: dict[str, Any]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_meta(meta_path: Path) -> dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_sdk_mode(mode: str | None, *, force: bool) -> str | None:
    if mode == "plan":
        return "plan"
    if mode == "ask":
        return "plan"
    if force:
        return "agent"
    return "agent"


def _build_agent_title(task_id: int, title: str, phase: str) -> str:
    return f"Bitrix24 №{task_id}: {title} [{phase}]"


def _sdk_worker(
    config: BoardConfig,
    *,
    worktree: Path,
    prompt: str,
    phase: str,
    mode: str | None,
    force: bool,
    task_id: int,
    title: str,
    log_path: Path,
    meta_path: Path,
) -> None:
    started_at = time.time()
    sdk_mode = _resolve_sdk_mode(mode, force=force)
    meta: dict[str, Any] = {
        "status": "starting",
        "phase": phase,
        "task_id": task_id,
        "title": title,
        "ui_title": _build_agent_title(task_id, title, phase),
        "backend": "sdk",
        "runtime": config.agent_runtime,
        "started_at": started_at,
    }
    _write_meta(meta_path, meta)

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    exit_code = 1
    cursor_agent_id: str | None = None
    cursor_run_id: str | None = None

    try:
        from cursor_sdk import Agent, AgentOptions, CloudAgentOptions, CloudRepository, LocalAgentOptions, SendOptions

        options_kwargs: dict[str, Any] = {
            "model": config.agent_model,
            "mode": sdk_mode,
        }
        if config.cursor_api_key:
            options_kwargs["api_key"] = config.cursor_api_key

        branch = branch_name(task_id)
        use_cloud = config.agent_runtime == "cloud" and bool(config.cloud_repo_url)
        if use_cloud:
            options_kwargs["cloud"] = CloudAgentOptions(
                repos=[
                    CloudRepository(
                        url=config.cloud_repo_url,
                        starting_ref=branch,
                    )
                ],
            )
        else:
            options_kwargs["local"] = LocalAgentOptions(cwd=str(worktree))

        agent = Agent.create(AgentOptions(**options_kwargs))
        try:
            cursor_agent_id = agent.agent_id
            meta.update(
                {
                    "status": "running",
                    "cursor_agent_id": cursor_agent_id,
                    "worktree": str(worktree),
                    "branch": branch,
                }
            )
            _write_meta(meta_path, meta)

            send_options = SendOptions(mode=sdk_mode) if sdk_mode else None
            run = agent.send(prompt, send_options)
            cursor_run_id = run.id
            meta["cursor_run_id"] = cursor_run_id
            _write_meta(meta_path, meta)

            for message in run.messages():
                if getattr(message, "type", None) == "assistant":
                    content = getattr(getattr(message, "message", None), "content", None) or []
                    for block in content:
                        if getattr(block, "type", None) == "text":
                            stdout_parts.append(getattr(block, "text", "") or "")

            result = run.wait()
            cursor_run_id = result.id or cursor_run_id
            final_text = getattr(result, "result", "") or ""
            if not final_text:
                try:
                    final_text = run.text() or ""
                except Exception:
                    final_text = ""

            if final_text:
                stdout_parts.append(final_text)

            stdout = "\n".join(part for part in stdout_parts if part).strip()
            exit_code = 0 if str(getattr(result, "status", "")).lower() not in {"error", "cancelled", "expired"} else 1
            meta.update(
                {
                    "status": "completed" if exit_code == 0 else "failed",
                    "cursor_run_id": cursor_run_id,
                    "result_status": getattr(result, "status", None),
                    "finished_at": time.time(),
                    "exit_code": exit_code,
                }
            )
        finally:
            agent.close()
    except Exception as exc:
        stderr_parts.append(str(exc))
        meta.update(
            {
                "status": "failed",
                "error": str(exc),
                "finished_at": time.time(),
                "exit_code": 1,
            }
        )
        exit_code = 1
    finally:
        if cursor_agent_id:
            meta["cursor_agent_id"] = cursor_agent_id
        _write_meta(meta_path, meta)
        with log_path.open("w", encoding="utf-8") as log_file:
            log_file.write(f"Backend: cursor-sdk ({config.agent_runtime})\n")
            log_file.write(f"UI title: {_build_agent_title(task_id, title, phase)}\n")
            if cursor_agent_id:
                log_file.write(f"Cursor agent ID: {cursor_agent_id}\n")
            if cursor_run_id:
                log_file.write(f"Cursor run ID: {cursor_run_id}\n")
            log_file.write(f"Worktree: {worktree}\n\n")
            if stdout_parts:
                log_file.write("=== STDOUT ===\n")
                log_file.write("\n".join(stdout_parts))
                log_file.write("\n")
            if stderr_parts:
                log_file.write("=== STDERR ===\n")
                log_file.write("\n".join(stderr_parts))
                log_file.write("\n")
            log_file.write(f"=== EXIT CODE: {exit_code} ===\n")


def _resolve_sdk_python() -> list[str]:
    explicit = os.environ.get("BITRIX_BOARD_SDK_PYTHON", "").strip()
    if explicit:
        return [explicit]
    for candidate in (
        "py -3.13",
        "py -3.12",
        "python3.13",
        "python3.12",
    ):
        if candidate.startswith("py "):
            version = candidate.split()[1]
            result = subprocess.run(
                ["py", version, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return ["py", version]
        else:
            found = shutil.which(candidate)
            if found:
                return [found]
    return [sys.executable]


def _spawn_sdk_agent(
    config: BoardConfig,
    *,
    worktree: Path,
    prompt: str,
    phase: str,
    mode: str | None,
    force: bool,
    task_id: int,
    title: str,
) -> SpawnedAgent:
    log_path, prompt_path, meta_path = _prepare_log_paths(worktree, phase)
    prompt_path.write_text(prompt, encoding="utf-8")
    payload_path = meta_path.with_suffix(".payload.json")
    payload_path.write_text(
        json.dumps(
            {
                "worktree": str(worktree),
                "prompt": prompt,
                "phase": phase,
                "mode": mode,
                "force": force,
                "task_id": task_id,
                "title": title,
                "log_path": str(log_path),
                "meta_path": str(meta_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    sdk_python = _resolve_sdk_python()
    command = [
        *sdk_python,
        "-m",
        "src.bitrix_board.sdk_worker",
        "--payload",
        str(payload_path),
    ]
    _write_meta(
        meta_path,
        {
            "status": "starting",
            "phase": phase,
            "task_id": task_id,
            "title": title,
            "ui_title": _build_agent_title(task_id, title, phase),
            "backend": "sdk",
            "runtime": config.agent_runtime,
            "command": command,
        },
    )

    process = subprocess.Popen(
        command,
        cwd=config.repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cursor_agent_id = None
    for _ in range(100):
        meta = _read_meta(meta_path)
        cursor_agent_id = meta.get("cursor_agent_id")
        status = meta.get("status")
        if cursor_agent_id or status in {"failed", "completed"}:
            break
        if process.poll() is not None and status not in ACTIVE_META_STATUSES:
            break
        time.sleep(0.2)

    return SpawnedAgent(
        pid=process.pid,
        log_path=log_path,
        phase=phase,
        meta_path=meta_path,
        cursor_agent_id=str(cursor_agent_id) if cursor_agent_id else None,
    )


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
    command.append("--trust")
    command.extend(["--output-format", "text"])
    return command


def _resolve_windows_agent_command(config: BoardConfig, command: list[str]) -> list[str]:
    agent_bin = Path(config.agent_bin)
    versions_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent" / "versions"
    if versions_dir.exists():
        version_dirs = sorted(
            [
                path
                for path in versions_dir.iterdir()
                if path.is_dir() and path.name[:1].isdigit()
            ],
            key=lambda path: path.name,
            reverse=True,
        )
        if version_dirs:
            node_exe = version_dirs[0] / "node.exe"
            index_js = version_dirs[0] / "index.js"
            if node_exe.exists() and index_js.exists():
                return [str(node_exe), str(index_js), *command[1:]]
    if agent_bin.suffix.lower() in {".cmd", ".bat"}:
        ps1 = agent_bin.with_suffix(".ps1")
        if ps1.exists():
            return [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1),
                *command[1:],
            ]
    return command


def _spawn_cli_agent(
    config: BoardConfig,
    *,
    worktree: Path,
    prompt: str,
    phase: str,
    mode: str | None = None,
    force: bool = False,
    task_id: int = 0,
    title: str = "",
) -> SpawnedAgent:
    log_path, prompt_path, meta_path = _prepare_log_paths(worktree, phase)
    prompt_path.write_text(prompt, encoding="utf-8")
    command = _build_command(config, prompt=prompt, mode=mode, force=force)
    if sys.platform.startswith("win"):
        command = _resolve_windows_agent_command(config, command)

    _write_meta(
        meta_path,
        {
            "status": "running",
            "phase": phase,
            "task_id": task_id,
            "title": title,
            "backend": "cli",
            "started_at": time.time(),
        },
    )

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
    return SpawnedAgent(pid=process.pid, log_path=log_path, phase=phase, meta_path=meta_path)


def spawn_agent(
    config: BoardConfig,
    *,
    worktree: Path,
    prompt: str,
    phase: str,
    mode: str | None = None,
    force: bool = False,
    task_id: int = 0,
    title: str = "",
) -> SpawnedAgent:
    use_sdk = config.agent_backend == "sdk" and bool(config.cursor_api_key)
    if use_sdk:
        return _spawn_sdk_agent(
            config,
            worktree=worktree,
            prompt=prompt,
            phase=phase,
            mode=mode,
            force=force,
            task_id=task_id,
            title=title,
        )
    return _spawn_cli_agent(
        config,
        worktree=worktree,
        prompt=prompt,
        phase=phase,
        mode=mode,
        force=force,
        task_id=task_id,
        title=title,
    )


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


def get_agent_meta(task_report: dict[str, Any] | None, log_path: str | None) -> dict[str, Any]:
    meta_path = (task_report or {}).get("cursor_meta_path")
    if meta_path:
        return _read_meta(Path(meta_path))
    if log_path:
        candidate = Path(log_path).parent.parent / ".bitrix-board"
        if candidate.exists():
            metas = sorted(candidate.glob("*.agent.json"), key=lambda path: path.stat().st_mtime, reverse=True)
            if metas:
                return _read_meta(metas[0])
    return {}


def is_agent_running(task_report: dict[str, Any] | None, log_path: str | None, agent_pid: int | None) -> bool:
    meta = get_agent_meta(task_report, log_path)
    if meta.get("status") in ACTIVE_META_STATUSES:
        return True
    if meta.get("status") in {"completed", "failed"}:
        return False
    if agent_pid and is_process_alive(agent_pid):
        return True
    return False


def is_process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                encoding="cp866",
                errors="replace",
                check=False,
            )
            stdout = result.stdout or ""
            return str(pid) in stdout
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
