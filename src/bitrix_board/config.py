from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

WEBHOOK_PATH_RE = re.compile(r"^/rest(?:/api)?/(\d+)/([^/]+)/?$")
DEFAULT_BITRIX_MCP_ENV = Path(r"C:\random_forest\bitrix mcp\.env")


@dataclass(frozen=True)
class WebhookConfig:
    base_url: str
    user_id: int
    origin: str
    token: str


@dataclass(frozen=True)
class BoardConfig:
    webhook: WebhookConfig
    poll_interval_seconds: int
    db_path: Path
    worktrees_dir: Path
    repo_root: Path
    max_review_cycles: int
    agent_bin: str
    default_group_id: int | None
    repo_group_map: dict[str, int]
    dispatcher_pid_path: Path
    plan_stage_name: str | None
    agent_backend: str
    agent_runtime: str
    cursor_api_key: str | None
    agent_model: str
    cloud_repo_url: str | None


def _parse_webhook_base(raw: str) -> WebhookConfig:
    trimmed = raw.strip()
    if not trimmed:
        raise ValueError("BITRIX_WEBHOOK_BASE is not set")

    from urllib.parse import urlparse

    parsed = urlparse(trimmed)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("BITRIX_WEBHOOK_BASE must use http or https")

    match = WEBHOOK_PATH_RE.match(parsed.path)
    if not match:
        raise ValueError(
            "BITRIX_WEBHOOK_BASE must match "
            "https://portal.example.ru/rest/{userId}/{token}"
        )

    user_id = int(match.group(1))
    token = match.group(2)
    base_url = trimmed.rstrip("/")
    return WebhookConfig(
        base_url=base_url,
        user_id=user_id,
        origin=f"{parsed.scheme}://{parsed.netloc}",
        token=token,
    )


def _load_repo_group_map() -> dict[str, int]:
    raw = os.environ.get("BITRIX_REPO_GROUP_MAP", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in parsed.items():
        try:
            group_id = int(value)
        except (TypeError, ValueError):
            continue
        if key and group_id > 0:
            result[str(key).lower()] = group_id
    return result


def _detect_repo_root() -> Path:
    explicit = os.environ.get("BITRIX_BOARD_REPO_ROOT", "").strip()
    if explicit:
        return Path(explicit).resolve()

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return Path.cwd().resolve()


def _resolve_agent_bin() -> str:
    explicit = os.environ.get("BITRIX_BOARD_AGENT_BIN", "").strip()
    if explicit:
        return explicit

    for candidate in ("agent", "cursor-agent"):
        found = shutil.which(candidate)
        if found:
            return found

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        for name in ("agent.exe", "cursor-agent.exe"):
            path = Path(local_app_data) / "cursor-agent" / name
            if path.exists():
                return str(path)

    return "agent"


def _detect_cloud_repo_url(repo_root: Path) -> str | None:
    explicit = os.environ.get("BITRIX_BOARD_CLOUD_REPO_URL", "").strip()
    if explicit:
        return explicit
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    if raw.startswith("git@"):
        host, path = raw.split(":", 1)
        host = host.removeprefix("git@")
        url = f"https://{host}/{path}"
        if url.endswith(".git"):
            return url[:-4]
        return url
    if raw.endswith(".git"):
        return raw[:-4]
    return raw


def load_config() -> BoardConfig:
    repo_root = _detect_repo_root()
    load_dotenv(repo_root / ".env.bitrix-board", override=False)
    if DEFAULT_BITRIX_MCP_ENV.exists():
        load_dotenv(DEFAULT_BITRIX_MCP_ENV, override=False)
    load_dotenv(repo_root / ".env", override=False)

    webhook_raw = os.environ.get("BITRIX_WEBHOOK_BASE", "")
    webhook = _parse_webhook_base(webhook_raw)

    default_group_raw = os.environ.get("BITRIX_DEFAULT_GROUP_ID", "").strip()
    default_group_id = int(default_group_raw) if default_group_raw.isdigit() else None

    db_path = Path(
        os.environ.get("BITRIX_BOARD_DB_PATH", str(repo_root / ".bitrix-board" / "state.db"))
    ).resolve()
    worktrees_dir = Path(
        os.environ.get("BITRIX_BOARD_WORKTREES_DIR", str(repo_root.parent / "worktrees"))
    ).resolve()

    return BoardConfig(
        webhook=webhook,
        poll_interval_seconds=max(5, int(os.environ.get("BITRIX_BOARD_POLL_INTERVAL_SECONDS", "60"))),
        db_path=db_path,
        worktrees_dir=worktrees_dir,
        repo_root=repo_root,
        max_review_cycles=max(1, int(os.environ.get("BITRIX_BOARD_MAX_REVIEW_CYCLES", "3"))),
        agent_bin=_resolve_agent_bin(),
        default_group_id=default_group_id,
        repo_group_map=_load_repo_group_map(),
        dispatcher_pid_path=repo_root / ".bitrix-board" / "dispatcher.pid",
        plan_stage_name=os.environ.get("BITRIX_PLAN_STAGE_NAME", "").strip() or None,
        agent_backend=os.environ.get("BITRIX_BOARD_AGENT_BACKEND", "sdk").strip().lower() or "sdk",
        agent_runtime=os.environ.get("BITRIX_BOARD_AGENT_RUNTIME", "local").strip().lower() or "local",
        cursor_api_key=os.environ.get("CURSOR_API_KEY", "").strip() or None,
        agent_model=os.environ.get("BITRIX_BOARD_AGENT_MODEL", "composer-2.5").strip() or "composer-2.5",
        cloud_repo_url=_detect_cloud_repo_url(repo_root),
    )
