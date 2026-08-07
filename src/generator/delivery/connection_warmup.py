"""Background warmup for a delivery connection after an error-rate trigger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete

from src.infra.db import session_scope
from src.infra.models import DeliveryChannelOutcome, DeliveryKeyGuard, SmtpMailbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _set_connection_progress(
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


def _set_progress(
    connection_id: str,
    *,
    key_guard_id: str | None,
    status: str,
    sent: int,
    errors: int,
    started: bool = False,
    completed: bool = False,
) -> None:
    if not key_guard_id:
        _set_connection_progress(
            connection_id,
            status=status,
            sent=sent,
            errors=errors,
            started=started,
            completed=completed,
        )
        return
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        guard = session.get(DeliveryKeyGuard, key_guard_id)
        if row is None or guard is None:
            return
        guard.warmup_status = status
        guard.warmup_sent_count = sent
        guard.warmup_error_count = errors
        if started and guard.warmup_started_at is None:
            guard.warmup_started_at = _now()
        if completed:
            guard.warmup_completed_at = _now()
        guard.updated_at = _now()
        from src.generator.delivery.channel_guard import _sync_key_guard_to_mailboxes

        _sync_key_guard_to_mailboxes(session, row, guard)


def _finalize_connection_warmup_failure(connection_id: str, message: str) -> None:
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


def finalize_connection_warmup_failure(
    connection_id: str,
    message: str,
    key_guard_id: str | None = None,
) -> None:
    if not key_guard_id:
        _finalize_connection_warmup_failure(connection_id, message)
        return
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        guard = session.get(DeliveryKeyGuard, key_guard_id)
        if row is None or guard is None:
            return
        guard.warmup_status = "failed"
        guard.warmup_completed_at = _now()
        guard.delivery_guard_state = "warmup"
        guard.delivery_guard_reason = (
            "Key warmup failed. Check the RuSender connection and reset the guard manually."
        )
        guard.updated_at = _now()
        from src.generator.delivery.channel_guard import _sync_key_guard_to_mailboxes

        _sync_key_guard_to_mailboxes(session, row, guard)


def run_connection_warmup(kwargs: dict[str, Any]) -> dict[str, Any]:
    connection_id = str(kwargs.get("connection_id") or "").strip()
    key_guard_id = str(kwargs.get("key_guard_id") or "").strip() or None
    owner_username = str(kwargs.get("owner_username") or "").strip()
    recipients = [
        str(value or "").strip().lower()
        for value in list(kwargs.get("recipients") or [])
        if str(value or "").strip()
    ]
    message_count = max(1, min(10000, int(kwargs.get("message_count") or 1)))
    tracking_run_id = str(kwargs.get("tracking_run_id") or "").strip()
    if not connection_id or not owner_username or not recipients:
        raise ValueError("Для прогрева не хватает подключения или адресов получателей.")

    _set_progress(
        connection_id,
        key_guard_id=key_guard_id,
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
                tracking_key=(
                    f"automatic-warmup:{connection_id}:{tracking_run_id}:"
                    f"{index + 1}:{recipient}"
                ),
            )
            sent += 1
        except Exception:
            errors += 1
        _set_progress(
            connection_id,
            key_guard_id=key_guard_id,
            status="running",
            sent=sent,
            errors=errors,
        )

    final_status = "completed" if errors == 0 else "failed"
    succeeded = errors == 0 and sent == message_count
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        guard = session.get(DeliveryKeyGuard, key_guard_id) if key_guard_id else None
        target: Any = guard or row
        if target is not None:
            target.warmup_status = final_status
            target.warmup_sent_count = sent
            target.warmup_error_count = errors
            target.warmup_completed_at = _now()
            if succeeded:
                target.delivery_guard_state = "normal"
                target.delivery_guard_reason = None
                target.delivery_guard_terminal_count = 0
                target.delivery_guard_error_count = 0
                target.delivery_guard_error_rate = 0.0
                target.delivery_guard_last_error_at = None
                outcome_scope = (
                    DeliveryChannelOutcome.delivery_key_guard_id == guard.id
                    if guard is not None
                    else DeliveryChannelOutcome.connection_id == connection_id
                )
                session.execute(delete(DeliveryChannelOutcome).where(outcome_scope))
            else:
                target.delivery_guard_state = "warmup"
                target.delivery_guard_reason = (
                    "Key warmup did not complete successfully; regular sending remains blocked."
                )
            target.updated_at = _now()
            if guard is not None and row is not None:
                from src.generator.delivery.channel_guard import _sync_key_guard_to_mailboxes

                _sync_key_guard_to_mailboxes(session, row, guard)

    return {
        "connection_id": connection_id,
        "status": final_status,
        "sent": sent,
        "errors": errors,
        "message_count": message_count,
    }
