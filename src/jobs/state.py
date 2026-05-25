from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .storage import resolve_job_paths


LEGACY_STATE_DIR = Path("data/state")


def _state_dir(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    if job_paths.uses_legacy_layout:
        return LEGACY_STATE_DIR
    return job_paths.root_dir / "state"


def resolve_state_path(agent_name: str, job_id: str | None = None) -> Path:
    return _state_dir(job_id) / f"{agent_name}.json"


def default_state_copy(default_state: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(default_state)


def load_agent_state(agent_name: str, default_state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    state = default_state_copy(default_state)
    path = resolve_state_path(agent_name, job_id)
    if not path.exists():
        return state
    try:
        # Manual PowerShell edits can leave a UTF-8 BOM; keep state recovery tolerant.
        stored = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return state
    if isinstance(stored, dict):
        state.update(stored)
    return state


def save_agent_state(agent_name: str, state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    path = resolve_state_path(agent_name, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return state

