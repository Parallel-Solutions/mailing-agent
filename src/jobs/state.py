from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .storage import resolve_job_paths


LEGACY_STATE_DIR = Path("data/state")
TERMINAL_STATE_STATUSES = {"completed", "error", "stopped"}
DETAIL_KEYS_BY_AGENT: dict[str, tuple[str, ...]] = {
    "generator": ("results",),
    "philologist": ("documents", "tool_trace", "tasks", "recent_events", "plan", "agent_loop", "tool_manifest"),
}
_STATE_WRITE_LOCKS: dict[str, threading.Lock] = {}
_STATE_WRITE_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _STATE_WRITE_LOCKS_GUARD:
        lock = _STATE_WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _STATE_WRITE_LOCKS[key] = lock
        return lock


def _state_dir(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    if job_paths.uses_legacy_layout:
        return LEGACY_STATE_DIR
    return job_paths.root_dir / "state"


def resolve_state_path(agent_name: str, job_id: str | None = None) -> Path:
    return _state_dir(job_id) / f"{agent_name}.json"


def resolve_state_details_path(agent_name: str, job_id: str | None = None) -> Path:
    return _state_dir(job_id) / f"{agent_name}.details.json"


def default_state_copy(default_state: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(default_state)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        stored = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return stored if isinstance(stored, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _path_lock(path)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    with lock:
        last_error: PermissionError | None = None
        for attempt in range(8):
            tmp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                tmp_path.write_text(text, encoding="utf-8")
                os.replace(tmp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.05 * (attempt + 1))
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

        # Windows can briefly lock files for indexing/antivirus or another worker.
        # If atomic replace keeps failing, prefer a direct state write over aborting the job.
        for attempt in range(3):
            try:
                path.write_text(text, encoding="utf-8")
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.1 * (attempt + 1))
        if last_error is not None:
            raise last_error


def _details_payload(agent_name: str, state: dict[str, Any]) -> dict[str, Any]:
    keys = DETAIL_KEYS_BY_AGENT.get(agent_name, ())
    return {key: deepcopy(state.get(key)) for key in keys if key in state}


def _compact_state_for_primary(agent_name: str, state: dict[str, Any], details_path: Path) -> dict[str, Any]:
    compact = deepcopy(state)
    details = _details_payload(agent_name, compact)
    if not details:
        return compact

    for key, value in details.items():
        if isinstance(value, list):
            compact[f"{key}_count"] = len(value)
            compact[key] = []
        elif isinstance(value, dict):
            compact[key] = {}
        else:
            compact[key] = None

    compact["details_path"] = str(details_path)
    return compact


def _should_split_state(agent_name: str, state: dict[str, Any]) -> bool:
    if agent_name not in DETAIL_KEYS_BY_AGENT:
        return False
    return str(state.get("status") or "") in TERMINAL_STATE_STATUSES


def load_agent_state(
    agent_name: str,
    default_state: dict[str, Any],
    job_id: str | None = None,
    *,
    include_details: bool = True,
) -> dict[str, Any]:
    state = default_state_copy(default_state)
    path = resolve_state_path(agent_name, job_id)
    if not path.exists():
        return state
    # Manual PowerShell edits can leave a UTF-8 BOM; keep state recovery tolerant.
    stored = _read_json(path)
    if isinstance(stored, dict):
        state.update(stored)

    if not include_details and _should_split_state(agent_name, state):
        details = _details_payload(agent_name, state)
        has_inline_details = any(bool(value) for value in details.values())
        if has_inline_details:
            details_path = resolve_state_details_path(agent_name, job_id)
            _write_json_atomic(details_path, details)
            state = _compact_state_for_primary(agent_name, state, details_path)
            _write_json_atomic(path, state)

    if include_details:
        details_path = resolve_state_details_path(agent_name, job_id)
        compact_details_path = state.get("details_path")
        if compact_details_path:
            details_path = Path(str(compact_details_path))
        if details_path.exists():
            state.update(_read_json(details_path))
    return state


def save_agent_state(agent_name: str, state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    path = resolve_state_path(agent_name, job_id)
    stored_state = state
    if _should_split_state(agent_name, state):
        details_path = resolve_state_details_path(agent_name, job_id)
        details = _details_payload(agent_name, state)
        has_inline_details = any(bool(value) for value in details.values())
        if has_inline_details:
            _write_json_atomic(details_path, details)
            stored_state = _compact_state_for_primary(agent_name, state, details_path)
    _write_json_atomic(path, stored_state)
    return state

