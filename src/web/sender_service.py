from __future__ import annotations

import json
from datetime import datetime
import secrets
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


def _read_sent_mail_log_items(job_id: object) -> list[dict]:
    job_id_text = str(job_id or "").strip()
    if not job_id_text:
        return []
    log_path = resolve_job_paths(job_id_text).sent_mail_log_path
    try:
        if not log_path.exists() or not log_path.is_file():
            return []
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    items: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _campaign_consent_log_totals(state: dict, stats: dict) -> dict[str, int]:
    if str(state.get("send_mode") or "").strip().lower() != "consent_request":
        return {}

    items = [
        item
        for item in _read_sent_mail_log_items(state.get("job_id"))
        if str(item.get("send_mode") or "").strip().lower() == "consent_request"
    ]
    if not items:
        return {}

    campaign_name = str(state.get("campaign_name") or "").strip()
    if campaign_name:
        campaign_items = [
            item
            for item in items
            if str(item.get("campaign_name") or "").strip() == campaign_name
        ]
        if campaign_items:
            items = campaign_items

    row_ids = {
        str(item.get("row_id") or "").strip()
        for item in items
        if str(item.get("row_id") or "").strip()
    }
    sent_rows = len(row_ids) if row_ids else len(items)
    if sent_rows <= 0:
        return {}

    error_rows = _safe_int(stats.get("error"))
    total_rows = max(
        _safe_int(stats.get("total")),
        _safe_int(state.get("total_rows")),
        sent_rows + error_rows,
    )
    return {
        "sent_rows": sent_rows,
        "error_rows": error_rows,
        "total_rows": total_rows,
        "email_sends": len(items),
    }

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
        "next_action": row.get("next_action"),
        "folder": row.get("folder"),
        "attachments": row.get("attachments") or [],
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
    send_mode = str(state.get("send_mode") or "materials")
    selection_scoped = bool(state.get("selection_scoped"))
    show_table_totals = (
        selection_scoped
        and status in {"completed", "stopped"}
        and mode == "send"
        and send_mode == "materials"
        and _safe_int(stats.get("total")) > 0
    )
    processed_rows = _safe_int(state.get("processed_rows"))
    ready_rows = _safe_int(state.get("ready_rows"))
    sent_rows = (
        _safe_int(state.get("sent_rows"))
        if send_mode == "consent_request" or (selection_scoped and not show_table_totals)
        else max(_safe_int(state.get("sent_rows")), _safe_int(stats.get("sent")))
    )
    error_rows = (
        _safe_int(state.get("error_rows"))
        if selection_scoped and not show_table_totals
        else max(_safe_int(state.get("error_rows")), _safe_int(stats.get("error")))
    )
    state_total_rows = _safe_int(state.get("total_rows"))
    total_rows = (
        max(state_total_rows, processed_rows)
        if (selection_scoped and not show_table_totals) or state_total_rows > 0
        else max(_safe_int(stats.get("total")), processed_rows)
    )
    if show_table_totals:
        total_rows = max(_safe_int(stats.get("total")), total_rows)
    campaign_log_totals = _campaign_consent_log_totals(state, stats)
    campaign_scope_applied = (
        bool(campaign_log_totals)
        and selection_scoped
        and status in {"completed", "stopped"}
        and mode == "send"
        and send_mode == "consent_request"
        and _safe_int(campaign_log_totals.get("sent_rows")) > sent_rows
    )
    if campaign_scope_applied:
        sent_rows = _safe_int(campaign_log_totals.get("sent_rows"))
        error_rows = max(error_rows, _safe_int(campaign_log_totals.get("error_rows")))
        total_rows = max(total_rows, _safe_int(campaign_log_totals.get("total_rows")))
    if status == "running":
        remaining_rows = max(0, total_rows - processed_rows)
    elif campaign_scope_applied:
        remaining_rows = max(0, total_rows - sent_rows - error_rows)
    elif selection_scoped and not show_table_totals:
        remaining_rows = max(0, total_rows - processed_rows)
    else:
        remaining_rows = _safe_int(state.get("remaining_rows"))
        if send_mode == "consent_request":
            remaining_rows = max(0, total_rows - processed_rows)
        elif remaining_rows <= 0 and mode == "send":
            remaining_rows = max(0, _safe_int(stats.get("pending")) + error_rows)
        elif remaining_rows <= 0:
            remaining_rows = max(0, total_rows - processed_rows)
    if status == "completed" and mode == "send":
        processed_rows = max(processed_rows, sent_rows + error_rows, total_rows - remaining_rows)
        if total_rows > 0:
            processed_rows = min(total_rows, processed_rows)
        ready_rows = max(ready_rows, sent_rows)
    summary_text = state.get("summary_text", "")
    if send_mode == "consent_request" and status == "completed" and mode == "send":
        if error_rows <= 0 and remaining_rows <= 0:
            summary_text = f"Запросы согласия отправлены. Отправлено: {sent_rows}."
        else:
            summary_text = (
                "Отправка запросов согласия завершена не полностью. "
                f"Отправлено: {sent_rows}. Не отправлено: {error_rows}. "
                f"Осталось в очереди: {remaining_rows}."
            )

    problem_rows = [
        row
        for row in rows
        if isinstance(row, dict) and (row.get("error") or str(row.get("result") or "").startswith(("error", "blocked", "needs_")))
    ]
    visible_rows = [*problem_rows, *[row for row in rows if isinstance(row, dict) and row not in problem_rows]]

    return {
        "status": status,
        "mode": mode,
        "send_mode": send_mode,
        "attachment_mode": state.get("attachment_mode", "kp"),
        "recipient_strategy": state.get("recipient_strategy", "all"),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
        "processed_rows": processed_rows,
        "ready_rows": ready_rows,
        "sent_rows": sent_rows,
        "error_rows": error_rows,
        "skipped_rows": state.get("skipped_rows", 0),
        "handoff_rows": state.get("handoff_rows", 0),
        "total_rows": total_rows,
        "summary_text": summary_text,
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
        "selection_scoped": selection_scoped,
        "remaining_rows": remaining_rows,
        "stop_requested": state.get("stop_requested", False),
        "stop_requested_at": state.get("stop_requested_at"),
        "transport": state.get("transport", "unisender"),
        "sender_email": state.get("sender_email", ""),
        "campaign_name": state.get("campaign_name", ""),
        "campaign_scope_applied": campaign_scope_applied,
        "campaign_email_sends": (
            _safe_int(campaign_log_totals.get("email_sends")) if campaign_scope_applied else 0
        ),
        "row_count": len(rows),
        "rows": [_compact_sender_row(row) for row in visible_rows[:20] if isinstance(row, dict)],
        "task_stats": state.get("task_stats", {}),
        "recent_events": (state.get("recent_events") or [])[:5],
    }


def run_sender_background(
    *,
    dry_run: bool = False,
    limit: int | None,
    transport: str | None,
    send_mode: str | None = None,
    attachment_mode: str | None = None,
    recipient_strategy: str | None = None,
    subject_template: str | None = None,
    sender_email: str | None = None,
    campaign_name: str | None = None,
    require_confirmed_consent: bool = False,
    work_type: str | None = None,
    job_id: str | None,
) -> None:
    try:
        _require("run_sender")(
            dry_run=dry_run,
            limit=limit,
            transport=transport,
            send_mode=send_mode,
            attachment_mode=attachment_mode,
            recipient_strategy=recipient_strategy,
            subject_template=subject_template,
            sender_email=sender_email,
            campaign_name=campaign_name,
            require_confirmed_consent=require_confirmed_consent,
            work_type=work_type,
            auto_recover=False,
            job_id=job_id,
        )
    except Exception as exc:
        _require("logger").exception("sender_background_failed", job_id=job_id, transport=transport)
        state = _require("load_sender_state")(job_id)
        state["status"] = "error"
        state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        state["summary_text"] = f"Агент-отправщик остановился с ошибкой: {type(exc).__name__}: {exc}"
        _require("save_sender_state")(state, job_id)
    finally:
        _require("unregister_sender_thread")(job_id)


def prime_sender_running_state(
    job_id: str | None,
    transport: str | None,
    attachment_mode: str | None = None,
    recipient_strategy: str | None = None,
    sender_email: str | None = None,
    campaign_name: str | None = None,
) -> dict:
    state = _require("load_sender_state")(job_id)
    stats = _require("collect_excel_stats")(resolve_job_paths(job_id).data_xlsx)
    total_rows = int(state.get("total_rows") or stats.get("total", 0) or 0)
    started_at = datetime.now().isoformat(timespec="seconds")
    state["status"] = "running"
    state["mode"] = "send"
    state["transport"] = transport or state.get("transport") or "smtp"
    state["attachment_mode"] = attachment_mode or state.get("attachment_mode") or "kp"
    state["recipient_strategy"] = recipient_strategy or state.get("recipient_strategy") or "all"
    state["sender_email"] = sender_email or state.get("sender_email") or ""
    state["campaign_name"] = campaign_name or state.get("campaign_name") or ""
    state["started_at"] = started_at
    state["send_run_id"] = f"send-{started_at.replace(':', '').replace('-', '')}-{secrets.token_hex(4)}"
    state["send_run_started_at"] = started_at
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


def prime_sender_checking_state(
    job_id: str | None,
    transport: str | None,
    attachment_mode: str | None = None,
    recipient_strategy: str | None = None,
    sender_email: str | None = None,
    campaign_name: str | None = None,
) -> dict:
    state = _require("load_sender_state")(job_id)
    stats = _require("collect_excel_stats")(resolve_job_paths(job_id).data_xlsx)
    total_rows = int(state.get("total_rows") or stats.get("total", 0) or 0)
    state["status"] = "running"
    state["mode"] = "dry_run"
    state["transport"] = transport or state.get("transport") or "unisender"
    state["attachment_mode"] = attachment_mode or state.get("attachment_mode") or "kp"
    state["recipient_strategy"] = recipient_strategy or state.get("recipient_strategy") or "all"
    state["sender_email"] = sender_email or state.get("sender_email") or ""
    state["campaign_name"] = campaign_name or state.get("campaign_name") or ""
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
