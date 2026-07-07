from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.infra.db import session_scope
from src.infra.models import AgentState
from src.jobs.storage import normalize_job_id, resolve_job_paths


LEGACY_JOB_ID = "__legacy__"
TERMINAL_STATE_STATUSES = {"completed", "error", "stopped"}
ALWAYS_SPLIT_DETAIL_AGENTS = {"philologist"}
DETAIL_KEYS_BY_AGENT: dict[str, tuple[str, ...]] = {
    "generator": ("results",),
    "philologist": ("documents", "tool_trace", "tasks", "recent_events", "plan", "agent_loop", "tool_manifest"),
}


def _storage_job_id(job_id: str | None) -> str:
    normalized = normalize_job_id(job_id)
    return normalized or LEGACY_JOB_ID


def resolve_state_path(agent_name: str, job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    if job_paths.uses_legacy_layout:
        return job_paths.root_dir / "state" / f"{agent_name}.json"
    return job_paths.root_dir / "state" / f"{agent_name}.json"


def resolve_state_details_path(agent_name: str, job_id: str | None = None) -> Path:
    return resolve_state_path(agent_name, job_id).with_name(f"{agent_name}.details.json")


def default_state_copy(default_state: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(default_state)


def _diagnostic_state(default_state: dict[str, Any], reason: str, message: str) -> dict[str, Any]:
    state = default_state_copy(default_state)
    state.update(
        {
            "status": "error",
            "state_error": reason,
            "state_error_type": "state_read_error",
            "state_error_message": message,
            "summary_text": (
                "Состояние агента повреждено или недоступно. "
                f"Автоматический сброс не выполнен, чтобы не потерять данные: {message}"
            ),
        }
    )
    return state


def _attach_details_read_error(state: dict[str, Any], message: str) -> dict[str, Any]:
    state["state_details_error"] = {
        "reason": "state_read_error",
        "type": "state_read_error",
        "message": message,
    }
    state["summary_text"] = (
        str(state.get("summary_text") or "").strip()
        + f"\n\nДетальное состояние недоступно: {message}"
    ).strip()
    return state


def _details_payload(agent_name: str, state: dict[str, Any]) -> dict[str, Any]:
    keys = DETAIL_KEYS_BY_AGENT.get(agent_name, ())
    return {key: deepcopy(state.get(key)) for key in keys if key in state}


def _compact_state_for_primary(agent_name: str, state: dict[str, Any]) -> dict[str, Any]:
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
    storage_job_id = _storage_job_id(job_id)
    with session_scope() as session:
        row = session.get(AgentState, {"job_id": storage_job_id, "agent_name": agent_name})
    if row is None:
        return state

    stored_state = row.state
    if not isinstance(stored_state, dict):
        return _diagnostic_state(default_state, "invalid_json_shape", "state JSON root must be an object")
    state.update(stored_state)

    if not include_details and _should_split_state(agent_name, state):
        details = _details_payload(agent_name, state)
        has_inline_details = any(bool(value) for value in details.values())
        if has_inline_details:
            compact = _compact_state_for_primary(agent_name, state)
            save_agent_state(agent_name, {**state, **compact}, job_id=job_id)
            state = compact

    if include_details and row.details and isinstance(row.details, dict):
        state.update(row.details)
    elif include_details and _should_split_state(agent_name, state) and not row.details:
        return _attach_details_read_error(state, "details missing in database")
    return state


def save_agent_state(agent_name: str, state: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    storage_job_id = _storage_job_id(job_id)
    stored_state = state
    details: dict[str, Any] | None = None
    if _should_split_state(agent_name, state):
        details = _details_payload(agent_name, state)
        has_inline_details = any(bool(value) for value in details.values())
        if has_inline_details:
            stored_state = _compact_state_for_primary(agent_name, state)
        else:
            details = None

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(AgentState, {"job_id": storage_job_id, "agent_name": agent_name})
        if row is None:
            row = AgentState(
                job_id=storage_job_id,
                agent_name=agent_name,
                state=stored_state,
                details=details,
                updated_at=now,
            )
            session.add(row)
        else:
            row.state = stored_state
            row.details = details
            row.updated_at = now
    return state
