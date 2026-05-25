from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .storage import resolve_job_paths


LEGACY_STATE_DIR = Path("data/state")
_STATE_LOCKS: dict[str, threading.Lock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


def _lock_for_path(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _STATE_LOCKS[key] = lock
        return lock


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
    payload = json.dumps(state, ensure_ascii=False, indent=2, default=str)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    with _lock_for_path(path):
        try:
            temp_path.write_text(payload, encoding="utf-8")
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
    return state

