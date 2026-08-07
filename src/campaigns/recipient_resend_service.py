"""Safe, user-triggered resend of one CampaignFlow recipient."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from src.campaigns.recipient_email_service import (
    parse_email_candidates,
    resolve_delivery_email,
    validate_delivery_email,
)
from src.infra.db import session_scope
from src.infra.models import (
    BackgroundTask,
    Campaign,
    CampaignRecipient,
    DeliveryAttempt,
    SuppressionEntry,
)
from src.workers.task_queue import ACTIVE_STATUSES, enqueue_task


TASK_TYPE = "recipient_resend"
RETRYABLE_MANAGER_STATUSES = {"delivery_error", "email_broken"}
AUTOMATIC_FALLBACK_GRACE_SECONDS = 90


class RecipientResendNotAllowed(RuntimeError):
    """Raised when a recipient cannot safely be sent again."""


def _now() -> datetime:
    return datetime.now(timezone.utc)



def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _blocked(reason: str, *, state: str = "blocked", retry_after: str | None = None) -> dict[str, Any]:
    return {
        "allowed": False,
        "target_email": "",
        "mode": "",
        "reason": reason,
        "retry_after": retry_after,
        "state": state,
    }


def _active_key(campaign_id: str, recipient_id: int) -> str:
    return f"{TASK_TYPE}:{campaign_id}:{recipient_id}"


def _campaign_recipient(job_id: str, row_id: str) -> tuple[Campaign | None, CampaignRecipient | None]:
    try:
        recipient_id = int(str(row_id or "").strip())
    except (TypeError, ValueError):
        return None, None
    with session_scope() as session:
        row = session.execute(
            select(Campaign, CampaignRecipient)
            .join(CampaignRecipient, CampaignRecipient.campaign_id == Campaign.id)
            .where(
                Campaign.job_id == str(job_id),
                CampaignRecipient.id == recipient_id,
            )
        ).first()
        if row is None:
            return None, None
        campaign, recipient = row
        session.expunge(campaign)
        session.expunge(recipient)
        return campaign, recipient


def _active_task(campaign_id: str, recipient_id: int) -> str:
    with session_scope() as session:
        task = session.execute(
            select(BackgroundTask).where(
                BackgroundTask.active_key == _active_key(campaign_id, recipient_id),
                BackgroundTask.status.in_(ACTIVE_STATUSES),
            )
        ).scalar_one_or_none()
        return str(task.id) if task is not None else ""


def _suppression(email: str) -> tuple[str, str | None]:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return "", None
    with session_scope() as session:
        row = session.get(SuppressionEntry, normalized)
        if row is None:
            return "", None
        if row.expires_at is not None and row.expires_at <= _now():
            session.delete(row)
            return "", None
        return (
            str(row.reason or ""),
            row.expires_at.isoformat(timespec="seconds") if row.expires_at is not None else None,
        )


def get_recipient_resend_capability(
    *,
    job_id: str,
    row_id: str,
    manager_status: str,
    failed_email: str,
    last_event_at: str = "",
) -> dict[str, Any]:
    """Return the authoritative resend decision for one statistics row."""

    campaign, recipient = _campaign_recipient(job_id, row_id)
    if campaign is None or recipient is None:
        return _blocked(
            "Повторная отправка доступна только для рассылок CampaignFlow.",
            state="unsupported",
        )
    if recipient.excluded:
        return _blocked("Получатель исключён из рассылки.")

    active = _active_task(campaign.id, int(recipient.id))
    if active:
        return {
            **_blocked("Повторная отправка уже поставлена в очередь.", state="queued"),
            "task_id": active,
        }

    status = str(manager_status or "").strip().lower()
    if status == "soft_bounce":
        reason, retry_after = _suppression(failed_email)
        message = "Провайдер уже выполняет автоматические повторы для временной ошибки."
        if reason == "soft_bounce" and retry_after:
            message = "Адрес временно в стоп-листе. Повтор станет доступен после указанного срока."
        return _blocked(message, state="automatic_retry", retry_after=retry_after)
    if status not in RETRYABLE_MANAGER_STATUSES:
        return _blocked("Текущий статус не допускает повторную отправку.")


    last_event = _parse_datetime(last_event_at)
    if last_event is not None:
        retry_at = last_event.timestamp() + AUTOMATIC_FALLBACK_GRACE_SECONDS
        if retry_at > _now().timestamp():
            retry_after = datetime.fromtimestamp(
                retry_at, tz=timezone.utc
            ).isoformat(timespec="seconds")
            return _blocked(
                "Автоматическая обработка ошибки ещё выполняется. Попробуйте повтор позже.",
                state="automatic_retry",
                retry_after=retry_after,
            )
    candidates = parse_email_candidates(recipient.email, recipient.email_fallback)
    normalized_failed = str(failed_email or "").strip().lower()
    target_email = ""
    mode = "same"

    if status == "email_broken":
        target_email, _attempts = resolve_delivery_email(
            recipient,
            skip_emails=[normalized_failed] if normalized_failed else None,
            owner_username=campaign.owner_username,
        )
        mode = "fallback"
        if not target_email:
            return _blocked(
                "Основной адрес не работает, а валидный запасной email не найден.",
                state="requires_new_email",
            )
    else:
        target_email = normalized_failed
        if target_email not in candidates:
            target_email = str((recipient.extra or {}).get("delivery_email") or "").strip().lower()
        if target_email not in candidates:
            target_email = candidates[0] if candidates else ""
        suppression_reason, retry_after = _suppression(target_email)
        if suppression_reason:
            state = "automatic_retry" if suppression_reason == "soft_bounce" else "blocked"
            return _blocked(
                f"Адрес находится в стоп-листе ({suppression_reason}).",
                state=state,
                retry_after=retry_after,
            )
        validation = validate_delivery_email(
            target_email, owner_username=campaign.owner_username
        )
        if not validation.is_valid:
            return _blocked(
                validation.reason or "Email не прошёл проверку.",
                state="requires_new_email",
            )
        target_email = validation.normalized_email

    return {
        "allowed": True,
        "target_email": target_email,
        "mode": mode,
        "reason": (
            "Письмо будет отправлено на запасной адрес."
            if mode == "fallback"
            else "Письмо будет повторно отправлено на тот же адрес."
        ),
        "retry_after": None,
        "state": "available",
        "campaign_id": campaign.id,
        "recipient_id": int(recipient.id),
        "send_scenario": campaign.send_scenario,
    }


def enqueue_recipient_resend(
    *,
    job_id: str,
    row_id: str,
    manager_status: str,
    failed_email: str,
    last_event_at: str = "",
    requested_by: str,
) -> dict[str, Any]:
    capability = get_recipient_resend_capability(
        job_id=job_id,
        row_id=row_id,
        manager_status=manager_status,
        failed_email=failed_email,
        last_event_at=last_event_at,
    )
    if capability.get("state") == "queued":
        return capability
    if not capability.get("allowed"):
        raise RecipientResendNotAllowed(
            str(capability.get("reason") or "Повторная отправка недоступна.")
        )

    request_id = str(uuid4())
    campaign_id = str(capability["campaign_id"])
    recipient_id = int(capability["recipient_id"])
    task, created = enqueue_task(
        task_type=TASK_TYPE,
        job_id=job_id,
        owner_username=requested_by,
        payload={
            "job_id": job_id,
            "campaign_id": campaign_id,
            "recipient_id": recipient_id,
            "target_email": str(capability["target_email"]),
            "manager_status": manager_status,
            "requested_by": requested_by,
            "send_run_id": f"manual-resend-{request_id}",
        },
        idempotency_key=f"{TASK_TYPE}:{request_id}",
        active_key=_active_key(campaign_id, recipient_id),
        max_attempts=2,
    )
    return {
        **capability,
        "allowed": False,
        "state": "queued",
        "reason": "Повторная отправка поставлена в очередь.",
        "task_id": str(task["id"]),
        "created": created,
    }


def _next_attempt_number(campaign_id: str, recipient_id: int) -> int:
    with session_scope() as session:
        latest = session.scalar(
            select(func.max(DeliveryAttempt.attempt_number)).where(
                DeliveryAttempt.campaign_id == campaign_id,
                DeliveryAttempt.recipient_id == recipient_id,
            )
        )
        return int(latest or 0) + 1


def run_recipient_resend(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one queued resend with a fresh provider idempotency scope."""

    campaign_id = str(payload.get("campaign_id") or "")
    recipient_id = int(payload.get("recipient_id") or 0)
    target_email = str(payload.get("target_email") or "").strip().lower()
    send_run_id = str(payload.get("send_run_id") or "").strip()
    if not campaign_id or not recipient_id or not target_email or not send_run_id:
        raise ValueError("Некорректные параметры повторной отправки.")

    with session_scope() as session:
        campaign = session.get(Campaign, campaign_id)
        recipient = session.get(CampaignRecipient, recipient_id)
        if campaign is None or recipient is None:
            raise ValueError("Рассылка или получатель не найдены.")
        if recipient.excluded:
            raise RecipientResendNotAllowed("Получатель исключён из рассылки.")
        if target_email not in parse_email_candidates(recipient.email, recipient.email_fallback):
            raise RecipientResendNotAllowed("Email больше не относится к этому получателю.")
        owner = campaign.owner_username
        job_id = campaign.job_id
        send_scenario = campaign.send_scenario

    suppression_reason, _retry_after = _suppression(target_email)
    if suppression_reason:
        raise RecipientResendNotAllowed(
            f"Адрес находится в стоп-листе ({suppression_reason})."
        )
    validation = validate_delivery_email(target_email, owner_username=owner)
    if not validation.is_valid:
        raise RecipientResendNotAllowed(validation.reason or "Email не прошёл проверку.")
    target_email = validation.normalized_email

    from src.campaigns.connection_service import campaign_connection_ids, pick_available_connection

    with session_scope() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError("Рассылка не найдена.")
        connection = pick_available_connection(
            campaign_connection_ids(campaign),
            owner,
            {},
            {},
            campaign=campaign,
        )
    if connection is None:
        raise RuntimeError("Нет доступного подключения отправителя.")

    attempt_number = _next_attempt_number(campaign_id, recipient_id)
    if send_scenario == "email_chain":
        from src.campaigns.chain_send_service import send_chain_node_email
        from src.campaigns.chain_service import get_email_chain
        from src.campaigns.service import record_delivery_attempt

        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise ValueError("Рассылка не найдена.")
            root_id = str(get_email_chain(campaign).get("root_node_id") or "")
        record_delivery_attempt(
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            batch_id=None,
            status="sending",
            delivery_email=target_email,
            attempt_number=attempt_number,
        )
        try:
            result = send_chain_node_email(
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                node_id=root_id,
                delivery_email_override=target_email,
                connection_id=connection.id,
                send_run_id=send_run_id,
            )
            record_delivery_attempt(
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                batch_id=None,
                status="sent",
                provider_message_id=str(result.get("message_id") or ""),
                delivery_email=target_email,
                attempt_number=attempt_number,
            )
        except Exception as exc:
            record_delivery_attempt(
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                batch_id=None,
                status="failed",
                error=str(exc),
                delivery_email=target_email,
                attempt_number=attempt_number,
            )
            raise
        return result

    from src.campaigns.delivery_fallback_service import resend_campaign_recipient_email
    from src.campaigns.service import _resolve_send_mode

    with session_scope() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError("Рассылка не найдена.")
        send_mode = _resolve_send_mode(campaign)
        subject = campaign.mail_subject or campaign.name

    try:
        message_id = resend_campaign_recipient_email(
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            delivery_email=target_email,
            send_mode=send_mode,
            subject=subject,
            transport=connection.transport,
            connection_id=connection.id,
            owner_username=owner,
            job_id=job_id,
            send_run_id=send_run_id,
            attempt_number=attempt_number,
        )
    except Exception as exc:
        from src.campaigns.service import record_delivery_attempt

        record_delivery_attempt(
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            batch_id=None,
            status="failed",
            error=str(exc),
            delivery_email=target_email,
            attempt_number=attempt_number,
        )
        raise
    return {
        "status": "sent",
        "message_id": message_id,
        "to": target_email,
        "attempt_number": attempt_number,
    }
