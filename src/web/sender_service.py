from __future__ import annotations

from datetime import datetime
from typing import Any

from src.jobs import resolve_job_paths

_deps: dict[str, Any] = {}


def configure_sender_service(**deps: Any) -> None:
    _deps.update(deps)


def _require(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"sender_service dependency is not configured: {name}")
    return value


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _compact_sender_row(row: dict) -> dict:
    attempts = row.get("attempts") if isinstance(row, dict) else []
    compact_attempts = []
    if isinstance(attempts, list):
        for attempt in attempts[:3]:
            if isinstance(attempt, dict):
                compact_attempts.append(
                    {
                        "recipient": attempt.get("recipient"),
                        "status": attempt.get("status"),
                        "error": attempt.get("error"),
                    }
                )

    return {
        "id": row.get("id"),
        "row_index": row.get("row_index"),
        "mun_name": row.get("mun_name"),
        "result": row.get("result"),
        "recipient": row.get("recipient"),
        "emails": row.get("emails") or [],
        "invalid_emails": row.get("invalid_emails") or [],
        "email_strategy": row.get("email_strategy"),
        "decision_reason": row.get("decision_reason"),
        "error": row.get("error"),
        "warning": row.get("warning"),
        "provider": row.get("provider"),
        "provider_status": row.get("provider_status"),
        "provider_status_label": row.get("provider_status_label"),
        "provider_message_id": row.get("provider_message_id"),
        "attempts": compact_attempts,
    }


def compact_sender_status(state: dict) -> dict:
    rows = state.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    stats = state.get("stats") or {}
    if not isinstance(stats, dict):
        stats = {}
    status = str(state.get("status") or "idle")
    mode = str(state.get("mode") or "dry_run")
    processed_rows = _safe_int(state.get("processed_rows"))
    ready_rows = _safe_int(state.get("ready_rows"))
    sent_rows = max(_safe_int(state.get("sent_rows")), _safe_int(stats.get("sent")))
    error_rows = max(_safe_int(state.get("error_rows")), _safe_int(stats.get("error")))
    total_rows = max(_safe_int(state.get("total_rows")), _safe_int(stats.get("total")), processed_rows)
    if status == "running":
        remaining_rows = max(0, total_rows - processed_rows)
    else:
        remaining_rows = _safe_int(state.get("remaining_rows"))
        if remaining_rows <= 0 and mode == "send":
            remaining_rows = max(0, _safe_int(stats.get("pending")) + error_rows)
        elif remaining_rows <= 0:
            remaining_rows = max(0, total_rows - processed_rows)

    return {
        "status": status,
        "mode": mode,
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
        "processed_rows": processed_rows,
        "ready_rows": ready_rows,
        "sent_rows": sent_rows,
        "error_rows": error_rows,
        "skipped_rows": state.get("skipped_rows", 0),
        "handoff_rows": state.get("handoff_rows", 0),
        "total_rows": total_rows,
        "summary_text": state.get("summary_text", ""),
        "stats": {
            "total": total_rows,
            "sent": sent_rows,
            "error": error_rows,
            "pending": remaining_rows,
        },
        "warning_rows": state.get("warning_rows", 0),
        "generator_handoff_rows": state.get("generator_handoff_rows", 0),
        "philology_blocked_rows": state.get("philology_blocked_rows", 0),
        "autonomous_recovery_rows": state.get("autonomous_recovery_rows", 0),
        "effective_limit": state.get("effective_limit"),
        "remaining_rows": remaining_rows,
        "stop_requested": state.get("stop_requested", False),
        "stop_requested_at": state.get("stop_requested_at"),
        "transport": state.get("transport", "unisender"),
        "row_count": len(rows),
        "rows": [_compact_sender_row(row) for row in rows[:20] if isinstance(row, dict)],
        "task_stats": state.get("task_stats", {}),
        "recent_events": (state.get("recent_events") or [])[:5],
    }


def run_sender_background(*, dry_run: bool = False, limit: int | None, transport: str | None, job_id: str | None) -> None:
    try:
        _require("run_sender")(dry_run=dry_run, limit=limit, transport=transport, auto_recover=False, job_id=job_id)
    except Exception as exc:
        _require("logger").exception("sender_background_failed", job_id=job_id, transport=transport)
        state = _require("load_sender_state")(job_id)
        state["status"] = "error"
        state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        state["summary_text"] = f"Агент-отправщик остановился с ошибкой: {type(exc).__name__}: {exc}"
        _require("save_sender_state")(state, job_id)
    finally:
        _require("unregister_sender_thread")(job_id)


def prime_sender_running_state(job_id: str | None, transport: str | None) -> dict:
    state = _require("load_sender_state")(job_id)
    stats = _require("collect_excel_stats")(resolve_job_paths(job_id).data_xlsx)
    total_rows = int(state.get("total_rows") or stats.get("total", 0) or 0)
    state["status"] = "running"
    state["mode"] = "send"
    state["transport"] = transport or state.get("transport") or "smtp"
    state["started_at"] = datetime.now().isoformat(timespec="seconds")
    state["completed_at"] = None
    state["processed_rows"] = 0
    state["ready_rows"] = 0
    state["sent_rows"] = int(stats.get("sent", 0))
    state["error_rows"] = 0
    state["skipped_rows"] = 0
    state["warning_rows"] = 0
    state["handoff_rows"] = 0
    state["generator_handoff_rows"] = 0
    state["philology_blocked_rows"] = 0
    state["autonomous_recovery_rows"] = 0
    state["rows"] = []
    state["stats"] = stats
    state["total_rows"] = total_rows
    state["remaining_rows"] = total_rows
    state["summary_text"] = "Агент-отправщик начал отправку писем."
    state["stop_requested"] = False
    state["stop_requested_at"] = None
    _require("save_sender_state")(state, job_id)
    return state


def prime_sender_checking_state(job_id: str | None, transport: str | None) -> dict:
    state = _require("load_sender_state")(job_id)
    stats = _require("collect_excel_stats")(resolve_job_paths(job_id).data_xlsx)
    total_rows = int(state.get("total_rows") or stats.get("total", 0) or 0)
    state["status"] = "running"
    state["mode"] = "dry_run"
    state["transport"] = transport or state.get("transport") or "unisender"
    state["started_at"] = datetime.now().isoformat(timespec="seconds")
    state["completed_at"] = None
    state["processed_rows"] = 0
    state["ready_rows"] = 0
    state["sent_rows"] = int(stats.get("sent", 0))
    state["error_rows"] = 0
    state["skipped_rows"] = 0
    state["warning_rows"] = 0
    state["handoff_rows"] = 0
    state["generator_handoff_rows"] = 0
    state["philology_blocked_rows"] = 0
    state["autonomous_recovery_rows"] = 0
    state["rows"] = []
    state["stats"] = stats
    state["total_rows"] = total_rows
    state["remaining_rows"] = total_rows
    state["summary_text"] = "Проверяю адреса и вложения. Письма пока не отправляются."
    state["stop_requested"] = False
    state["stop_requested_at"] = None
    _require("save_sender_state")(state, job_id)
    return state
