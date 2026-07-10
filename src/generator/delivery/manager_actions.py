from __future__ import annotations

from datetime import datetime
from typing import Any

from src.jobs.job_docs import append_event, read_events
from src.jobs.storage import normalize_job_id

MANAGER_ACTION_STREAM = "sender_manager_actions"
REPORT_HISTORY_STREAM = "sender_reports"

ACTION_TYPES = {
    "call": "Перезвонить",
    "resend": "Повторить отправку",
    "find_another_email": "Найти другой email",
    "create_task": "Создать задачу",
    "manual_check": "Проверить вручную",
    "do_not_contact": "Не трогать",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _recipient_key(row_id: str, recipient_email: str) -> tuple[str, str]:
    return _safe_text(row_id), _safe_text(recipient_email).lower()


def append_manager_action(
    job_id: str | None,
    *,
    row_id: str,
    recipient_email: str,
    organization: str = "",
    recipient_name: str = "",
    action_type: str,
    responsible_manager: str = "",
    due_at: str = "",
    comment: str = "",
    priority: bool = False,
    created_by: str = "",
) -> dict[str, Any]:
    normalized_action = _safe_text(action_type).lower()
    if normalized_action not in ACTION_TYPES:
        raise ValueError(f"Unsupported manager action type: {action_type}")

    record: dict[str, Any] = {
        "created_at": _now_iso(),
        "job_id": normalize_job_id(job_id) or "",
        "row_id": _safe_text(row_id),
        "recipient_email": _safe_text(recipient_email).lower(),
        "organization": _safe_text(organization),
        "recipient_name": _safe_text(recipient_name),
        "action_type": normalized_action,
        "action_label": ACTION_TYPES[normalized_action],
        "responsible_manager": _safe_text(responsible_manager),
        "due_at": _safe_text(due_at),
        "comment": _safe_text(comment),
        "priority": bool(priority),
        "created_by": _safe_text(created_by),
    }
    append_event(job_id, MANAGER_ACTION_STREAM, record)
    if normalized_action == "create_task":
        _dispatch_external_task(record)
    return record


def load_manager_actions(job_id: str | None) -> list[dict[str, Any]]:
    return [dict(item) for item in read_events(job_id, MANAGER_ACTION_STREAM) if isinstance(item, dict)]


def latest_action_by_recipient(job_id: str | None) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in load_manager_actions(job_id):
        key = _recipient_key(record.get("row_id"), record.get("recipient_email"))
        if not key[0] or not key[1]:
            continue
        previous = latest.get(key)
        if not previous or _safe_text(record.get("created_at")) >= _safe_text(previous.get("created_at")):
            latest[key] = record
    return latest


def append_report_history(
    job_id: str | None,
    *,
    report_id: str,
    report_type: str,
    period_from: str = "",
    period_to: str = "",
    fmt: str,
    author: str = "",
    status: str = "ready",
    path: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "created_at": _now_iso(),
        "report_id": _safe_text(report_id),
        "report_type": _safe_text(report_type),
        "period_from": _safe_text(period_from),
        "period_to": _safe_text(period_to),
        "format": _safe_text(fmt).lower(),
        "author": _safe_text(author),
        "status": _safe_text(status) or "ready",
        "path": _safe_text(path),
        "options": options or {},
    }
    append_event(job_id, REPORT_HISTORY_STREAM, record)
    return record


def load_report_history(job_id: str | None) -> list[dict[str, Any]]:
    return [dict(item) for item in read_events(job_id, REPORT_HISTORY_STREAM) if isinstance(item, dict)]


def _dispatch_external_task(record: dict[str, Any]) -> None:
    """Extension point for Bitrix24 or other task systems."""
    return None
