from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .json_store import JsonReadResult, read_json, write_json_atomic
from .storage import resolve_job_paths


LEGACY_STATE_DIR = Path("data/state")
TERMINAL_STATE_STATUSES = {"completed", "error", "stopped"}
ALWAYS_SPLIT_DETAIL_AGENTS = {"philologist"}
DETAIL_KEYS_BY_AGENT: dict[str, tuple[str, ...]] = {
    "generator": ("results",),
    "philologist": ("documents", "tool_trace", "tasks", "recent_events", "plan", "agent_loop", "tool_manifest"),
}


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


def _read_state_json(path: Path) -> JsonReadResult:
    result = read_json(path, default={})
    if result.ok and not isinstance(result.data, dict):
        return JsonReadResult({}, error="state JSON root must be an object", error_type="invalid_json_shape")
    return result


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload, trailing_newline=False)


def _diagnostic_state(default_state: dict[str, Any], path: Path, result: JsonReadResult) -> dict[str, Any]:
    state = default_state_copy(default_state)
    reason = "corrupt_json" if result.error_type in {"json_decode", "invalid_json_shape"} else "state_read_error"
    state.update(
        {
            "status": "error",
            "state_error": reason,
            "state_error_type": result.error_type,
            "state_error_path": str(path),
            "state_error_message": result.error,
            "summary_text": (
                "Файл состояния поврежден или недоступен. "
                f"Автоматический сброс не выполнен, чтобы не потерять данные: {path}"
            ),
        }
    )
    return state


def _attach_details_read_error(state: dict[str, Any], path: Path, result: JsonReadResult) -> dict[str, Any]:
    state["state_details_error"] = {
        "reason": "corrupt_json" if result.error_type in {"json_decode", "invalid_json_shape"} else "state_read_error",
        "type": result.error_type,
        "path": str(path),
        "message": result.error,
    }
    state["summary_text"] = (
        str(state.get("summary_text") or "").strip()
        + f"\n\nДетальный файл состояния поврежден или недоступен: {path}"
    ).strip()
    return state


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
    if agent_name in ALWAYS_SPLIT_DETAIL_AGENTS:
        return True
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
    stored_result = _read_state_json(path)
    if not stored_result.ok:
        return _diagnostic_state(default_state, path, stored_result)
    state.update(stored_result.data)

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
            details_result = _read_state_json(details_path)
            if not details_result.ok:
                return _attach_details_read_error(state, details_path, details_result)
            state.update(details_result.data)
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
