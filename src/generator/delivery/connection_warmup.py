"""Background warmup for a delivery connection after an error-rate trigger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete

from src.infra.db import session_scope
from src.infra.models import DeliveryChannelOutcome, SmtpMailbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _set_progress(
    connection_id: str,
    *,
    status: str,
    sent: int,
    errors: int,
    started: bool = False,
    completed: bool = False,
) -> None:
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None:
            return
        row.warmup_status = status
        row.warmup_sent_count = sent
        row.warmup_error_count = errors
        if started and row.warmup_started_at is None:
            row.warmup_started_at = _now()
        if completed:
            row.warmup_completed_at = _now()
        row.updated_at = _now()


def finalize_connection_warmup_failure(connection_id: str, message: str) -> None:
    del message
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None:
            return
        row.warmup_status = "failed"
        row.warmup_completed_at = _now()
        row.delivery_guard_state = "warmup"
        row.delivery_guard_reason = "Прогрев не удалось завершить. Проверьте подключение и сбросьте состояние прогрева."
        row.last_error = None
        row.updated_at = _now()


def run_connection_warmup(kwargs: dict[str, Any]) -> dict[str, Any]:
    connection_id = str(kwargs.get("connection_id") or "").strip()
    owner_username = str(kwargs.get("owner_username") or "").strip()
    recipients = [
        str(value or "").strip().lower()
        for value in list(kwargs.get("recipients") or [])
        if str(value or "").strip()
    ]
    message_count = max(1, min(10000, int(kwargs.get("message_count") or 1)))
    if not connection_id or not owner_username or not recipients:
        raise ValueError("Для прогрева не хватает подключения или адресов получателей.")

    _set_progress(
        connection_id,
        status="running",
        sent=0,
        errors=0,
        started=True,
    )

    from src.campaigns.batch_worker import _send_delivery_message

    sent = 0
    errors = 0
    for index in range(message_count):
        recipient = recipients[index % len(recipients)]
        try:
            _send_delivery_message(
                connection_id=connection_id,
                owner_username=owner_username,
                to_email=recipient,
                subject=f"Прогрев почтового подключения — письмо {index + 1}",
                html=(
                    "<p>Это автоматическое письмо прогрева почтового подключения.</p>"
                    "<p>Отвечать на него не требуется.</p>"
                ),
                text=(
                    "Это автоматическое письмо прогрева почтового подключения. "
                    "Отвечать на него не требуется."
                ),
                row_id=f"warmup-{index + 1}",
                send_mode="connection_warmup",
                track_links=False,
            )
            sent += 1
        except Exception:
            errors += 1
        _set_progress(
            connection_id,
            status="running",
            sent=sent,
            errors=errors,
        )

    final_status = (
        "completed"
        if errors == 0
        else "completed_with_errors"
        if sent > 0
        else "failed"
    )
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is not None:
            row.warmup_status = final_status
            row.warmup_sent_count = sent
            row.warmup_error_count = errors
            row.warmup_completed_at = _now()
            row.delivery_guard_state = "normal"
            row.delivery_guard_reason = None
            row.delivery_guard_terminal_count = 0
            row.delivery_guard_error_count = 0
            row.delivery_guard_error_rate = 0.0
            row.delivery_guard_last_error_at = None
            row.updated_at = _now()
            session.execute(
                delete(DeliveryChannelOutcome).where(
                    DeliveryChannelOutcome.connection_id == connection_id
                )
            )

    return {
        "connection_id": connection_id,
        "status": final_status,
        "sent": sent,
        "errors": errors,
        "message_count": message_count,
    }
