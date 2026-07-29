"""Database-backed delivery error guard and channel-wide rate limiter."""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select

from src.campaigns.state import transition_campaign_status
from src.infra.db import session_scope
from src.infra.models import (
    Campaign,
    CampaignBatch,
    DeliveryChannelOutcome,
    DeliveryChannelSendSlot,
    SmtpMailbox,
)
from src.utils.logger import logger


SUCCESS_STATUSES = {"delivered", "ok_delivered", "accepted"}
ERROR_STATUSES = {
    "hard_bounced",
    "hard_bounce",
    "soft_bounced",
    "soft_bounce",
    "bounced",
    "err_delivery_failed",
    "err_user_unknown",
    "err_mailbox_full",
    "err_recipient_inactive",
    "delivery_failed",
    "failed",
    "error",
    "rejected",
    "undelivered",
    "not_delivered",
}
ACTIVE_CAMPAIGN_STATUSES = {"scheduled", "running"}
GUARD_STATES = {"normal", "throttled", "disabled", "warmup"}
GUARD_ACTIONS = {"throttle", "disable", "warmup"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DeliveryChannelDisabled(RuntimeError):
    """Raised when a delivery channel was disabled by its error guard."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value: Any, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    result = max(minimum, result)
    return min(result, maximum) if maximum is not None else result


def _safe_float(value: Any, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return min(maximum, max(minimum, result))


def normalize_guard_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Return only explicitly supplied, validated guard settings."""
    normalized: dict[str, Any] = {}
    if "delivery_guard_enabled" in data and data.get("delivery_guard_enabled") is not None:
        normalized["delivery_guard_enabled"] = bool(data.get("delivery_guard_enabled"))
    if "delivery_error_rate_threshold" in data and data.get("delivery_error_rate_threshold") is not None:
        normalized["delivery_error_rate_threshold"] = _safe_float(
            data.get("delivery_error_rate_threshold"),
            0.05,
            minimum=0.001,
            maximum=1.0,
        )
    if "delivery_error_window_minutes" in data and data.get("delivery_error_window_minutes") is not None:
        normalized["delivery_error_window_minutes"] = _safe_int(
            data.get("delivery_error_window_minutes"), 60, minimum=5, maximum=10080
        )
    if "delivery_error_min_samples" in data and data.get("delivery_error_min_samples") is not None:
        normalized["delivery_error_min_samples"] = _safe_int(
            data.get("delivery_error_min_samples"), 20, minimum=1, maximum=1000000
        )
    if "delivery_error_critical_count" in data and data.get("delivery_error_critical_count") is not None:
        normalized["delivery_error_critical_count"] = _safe_int(
            data.get("delivery_error_critical_count"), 10, minimum=0, maximum=1000000
        )
    if "delivery_throttled_max_per_hour" in data and data.get("delivery_throttled_max_per_hour") is not None:
        normalized["delivery_throttled_max_per_hour"] = _safe_int(
            data.get("delivery_throttled_max_per_hour"), 50, minimum=1, maximum=1000000
        )
    if "delivery_error_action" in data and data.get("delivery_error_action") is not None:
        action = str(data.get("delivery_error_action") or "").strip().lower()
        if action not in GUARD_ACTIONS:
            raise ValueError("Неизвестное действие контроля ошибок.")
        normalized["delivery_error_action"] = action
    if "warmup_recipients" in data and data.get("warmup_recipients") is not None:
        raw = data.get("warmup_recipients")
        values = raw if isinstance(raw, list) else re.split(r"[,;\n]+", str(raw or ""))
        recipients: list[str] = []
        for value in values:
            email = str(value or "").strip().lower()
            if not email:
                continue
            if not EMAIL_RE.fullmatch(email):
                raise ValueError(f"Некорректный адрес для прогрева: {email}")
            if email not in recipients:
                recipients.append(email)
        normalized["warmup_recipients"] = recipients
    if "warmup_percent_of_errors" in data and data.get("warmup_percent_of_errors") is not None:
        normalized["warmup_percent_of_errors"] = _safe_int(
            data.get("warmup_percent_of_errors"),
            100,
            minimum=1,
            maximum=10000,
        )
    return normalized


def apply_guard_settings(row: SmtpMailbox, data: dict[str, Any]) -> None:
    for key, value in normalize_guard_settings(data).items():
        setattr(row, key, value)
    if bool(row.delivery_guard_enabled) and str(row.delivery_error_action or "") == "warmup":
        if not list(row.warmup_recipients or []):
            raise ValueError("Укажите хотя бы один адрес получателя для прогрева.")
    if not bool(row.delivery_guard_enabled):
        row.delivery_guard_state = "normal"
        row.delivery_guard_reason = None
        row.delivery_guard_triggered_at = None
        if row.status == "disabled_by_guard":
            row.status = "active"
            row.last_error = None
    elif row.delivery_guard_state == "disabled":
        row.status = "disabled_by_guard"
        row.last_error = row.delivery_guard_reason


def _effective_hour_limit(row: SmtpMailbox) -> int:
    configured = max(0, int(row.max_per_hour or 0))
    if row.delivery_guard_state != "throttled":
        return configured
    throttled = max(1, int(row.delivery_throttled_max_per_hour or 50))
    return min(configured, throttled) if configured > 0 else throttled


def guard_snapshot(row: SmtpMailbox) -> dict[str, Any]:
    return {
        "enabled": bool(row.delivery_guard_enabled),
        "state": str(row.delivery_guard_state or "normal"),
        "reason": str(row.delivery_guard_reason or ""),
        "error_rate_threshold": float(row.delivery_error_rate_threshold or 0.05),
        "window_minutes": int(row.delivery_error_window_minutes or 60),
        "min_samples": int(row.delivery_error_min_samples or 20),
        "critical_error_count": int(row.delivery_error_critical_count or 0),
        "action": str(row.delivery_error_action or "warmup"),
        "throttled_max_per_hour": int(row.delivery_throttled_max_per_hour or 50),
        "terminal_count": int(row.delivery_guard_terminal_count or 0),
        "error_count": int(row.delivery_guard_error_count or 0),
        "error_rate": float(row.delivery_guard_error_rate or 0.0),
        "effective_max_per_hour": _effective_hour_limit(row),
        "triggered_at": row.delivery_guard_triggered_at.isoformat() if row.delivery_guard_triggered_at else "",
        "last_error_at": row.delivery_guard_last_error_at.isoformat() if row.delivery_guard_last_error_at else "",
        "warmup_recipients": list(row.warmup_recipients or []),
        "warmup_percent_of_errors": int(row.warmup_percent_of_errors or 100),
        "warmup_task_id": str(row.warmup_task_id or ""),
        "warmup_status": str(row.warmup_status or "idle"),
        "warmup_sent_count": int(row.warmup_sent_count or 0),
        "warmup_error_count": int(row.warmup_error_count or 0),
        "warmup_started_at": row.warmup_started_at.isoformat() if row.warmup_started_at else "",
        "warmup_completed_at": row.warmup_completed_at.isoformat() if row.warmup_completed_at else "",
    }


def get_channel_guard(connection_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None:
            raise LookupError("Delivery connection not found.")
        return guard_snapshot(row)


def _parse_occurred_at(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return _now()


def _classify_outcome(provider_status: str) -> str | None:
    normalized = str(provider_status or "").strip().lower()
    if normalized in SUCCESS_STATUSES:
        return "success"
    if normalized in ERROR_STATUSES:
        return "error"
    return None


def _campaign_uses_connection(campaign: Campaign, connection_id: str) -> bool:
    connection_ids = [str(item) for item in list(campaign.connection_ids or [])]
    return connection_id in connection_ids or str(campaign.smtp_mailbox_id or "") == connection_id


def _pause_campaigns_for_channel(
    connection_id: str,
    *,
    reason: str = "delivery_channel_disabled",
) -> int:
    task_ids: set[str] = set()
    paused = 0
    with session_scope() as session:
        campaigns = session.scalars(
            select(Campaign).where(Campaign.status.in_(ACTIVE_CAMPAIGN_STATUSES))
        ).all()
        campaign_ids: list[str] = []
        now = _now()
        for campaign in campaigns:
            if not _campaign_uses_connection(campaign, connection_id):
                continue
            transition_campaign_status(
                session,
                campaign,
                "paused",
                reason=reason,
                actor="delivery_channel_guard",
                at=now,
            )
            campaign_ids.append(campaign.id)
            paused += 1
        if campaign_ids:
            batches = session.scalars(
                select(CampaignBatch).where(
                    CampaignBatch.campaign_id.in_(campaign_ids),
                    CampaignBatch.status.in_(["pending", "running"]),
                )
            ).all()
            for batch in batches:
                batch.status = "paused"
                if batch.task_id:
                    task_ids.add(batch.task_id)

    if task_ids:
        from src.workers.task_queue import request_cancel

        for task_id in task_ids:
            try:
                request_cancel(task_id)
            except Exception:
                pass
    return paused


def _evaluate_locked(session, row: SmtpMailbox, *, now: datetime) -> str | None:
    window_start = now - timedelta(minutes=max(5, int(row.delivery_error_window_minutes or 60)))
    counts = session.execute(
        select(
            func.count(DeliveryChannelOutcome.id),
            func.count(DeliveryChannelOutcome.id).filter(DeliveryChannelOutcome.outcome == "error"),
        ).where(
            DeliveryChannelOutcome.connection_id == row.id,
            DeliveryChannelOutcome.occurred_at >= window_start,
        )
    ).one()
    terminal_count = int(counts[0] or 0)
    error_count = int(counts[1] or 0)
    error_rate = float(error_count / terminal_count) if terminal_count else 0.0
    row.delivery_guard_terminal_count = terminal_count
    row.delivery_guard_error_count = error_count
    row.delivery_guard_error_rate = error_rate

    if not bool(row.delivery_guard_enabled) or row.delivery_guard_state in {"throttled", "disabled", "warmup"}:
        return None

    rate_triggered = (
        terminal_count >= max(1, int(row.delivery_error_min_samples or 20))
        and error_rate > float(row.delivery_error_rate_threshold or 0.05)
    )
    critical = max(0, int(row.delivery_error_critical_count or 0))
    count_triggered = critical > 0 and error_count >= critical
    if not rate_triggered and not count_triggered:
        return None

    reasons: list[str] = []
    if rate_triggered:
        reasons.append(
            f"Доля ошибок доставки {error_rate:.2%} превысила "
            f"{float(row.delivery_error_rate_threshold):.2%}"
        )
    if count_triggered:
        reasons.append(f"Количество ошибок доставки {error_count} достигло критического значения {critical}")
    action = str(row.delivery_error_action or "warmup")
    if action == "warmup":
        row.delivery_guard_state = "warmup"
        row.warmup_status = "queued"
        row.warmup_sent_count = 0
        row.warmup_error_count = 0
        row.warmup_started_at = None
        row.warmup_completed_at = None
    else:
        row.delivery_guard_state = "disabled" if action == "disable" else "throttled"
    row.delivery_guard_reason = "; ".join(reasons)
    row.delivery_guard_triggered_at = now
    if row.delivery_guard_state == "disabled":
        row.status = "disabled_by_guard"
        row.last_error = row.delivery_guard_reason
    return action


def _enqueue_connection_warmup(
    connection_id: str,
    *,
    error_count: int,
    triggered_at: datetime,
) -> str:
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None:
            raise LookupError("Delivery connection not found.")
        recipients = list(row.warmup_recipients or [])
        percent = max(1, int(row.warmup_percent_of_errors or 100))
        message_count = min(
            10000,
            max(1, int(math.ceil(max(1, error_count) * percent / 100.0))),
        )
        owner_username = str(row.owner_username or "")

    from src.workers.task_queue import enqueue_task

    task, _created = enqueue_task(
        task_type="connection_warmup",
        job_id=None,
        owner_username=owner_username,
        payload={
            "connection_id": connection_id,
            "owner_username": owner_username,
            "recipients": recipients,
            "message_count": message_count,
        },
        max_attempts=1,
        idempotency_key=f"connection_warmup:{connection_id}:{triggered_at.isoformat()}",
        active_key=f"connection_warmup:{connection_id}",
    )
    task_id = str(task["id"])
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is not None:
            row.warmup_task_id = task_id
            row.warmup_status = "queued"
            row.updated_at = _now()
    return task_id


def record_channel_outcome(
    *,
    connection_id: str,
    provider_message_id: str,
    provider_status: str,
    recipient: str = "",
    smtp_response: str = "",
    occurred_at: datetime | str | None = None,
) -> dict[str, Any] | None:
    """Upsert a terminal delivery result and evaluate the channel guard."""
    outcome = _classify_outcome(provider_status)
    message_id = str(provider_message_id or "").strip()
    if outcome is None or not connection_id or not message_id:
        return None

    timestamp = _parse_occurred_at(occurred_at)
    triggered_action: str | None = None
    with session_scope() as session:
        row = session.execute(
            select(SmtpMailbox).where(SmtpMailbox.id == connection_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            return None
        existing = session.scalar(
            select(DeliveryChannelOutcome).where(
                DeliveryChannelOutcome.connection_id == connection_id,
                DeliveryChannelOutcome.provider_message_id == message_id,
            )
        )
        if existing is None:
            session.add(
                DeliveryChannelOutcome(
                    connection_id=connection_id,
                    provider_message_id=message_id,
                    recipient=str(recipient or "").strip().lower(),
                    provider_status=str(provider_status or "").strip().lower(),
                    outcome=outcome,
                    smtp_response=str(smtp_response or "").strip() or None,
                    occurred_at=timestamp,
                )
            )
        else:
            existing.recipient = str(recipient or existing.recipient or "").strip().lower()
            existing.provider_status = str(provider_status or "").strip().lower()
            existing.outcome = outcome
            existing.smtp_response = str(smtp_response or existing.smtp_response or "").strip() or None
            existing.occurred_at = timestamp
            existing.updated_at = _now()
        if outcome == "error":
            row.delivery_guard_last_error_at = timestamp
        session.flush()
        triggered_action = _evaluate_locked(session, row, now=_now())
        snapshot = guard_snapshot(row)

    if triggered_action in {"disable", "warmup"}:
        snapshot["paused_campaigns"] = _pause_campaigns_for_channel(
            connection_id,
            reason=(
                "delivery_channel_warmup"
                if triggered_action == "warmup"
                else "delivery_channel_disabled"
            ),
        )
    if triggered_action == "warmup":
        try:
            snapshot["warmup_task_id"] = _enqueue_connection_warmup(
                connection_id,
                error_count=int(snapshot.get("error_count") or 0),
                triggered_at=timestamp,
            )
            snapshot["warmup_status"] = "queued"
        except Exception:
            logger.exception(
                "connection_warmup_enqueue_failed",
                connection_id=connection_id,
            )
            with session_scope() as session:
                row = session.get(SmtpMailbox, connection_id)
                if row is not None:
                    row.warmup_status = "failed"
                    row.delivery_guard_reason = (
                        "Не удалось поставить прогрев в очередь. "
                        "Проверьте настройки и сбросьте состояние прогрева."
                    )
                    row.updated_at = _now()
            snapshot["warmup_status"] = "failed"
    return snapshot


def reset_channel_guard(connection_id: str, *, enable: bool | None = None) -> dict[str, Any]:
    """Reset counters/state without auto-resuming campaigns."""
    warmup_task_id: str | None = None
    with session_scope() as session:
        row = session.execute(
            select(SmtpMailbox).where(SmtpMailbox.id == connection_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise LookupError("Delivery connection not found.")
        if enable is not None:
            row.delivery_guard_enabled = bool(enable)
        warmup_task_id = str(row.warmup_task_id or "") or None
        row.delivery_guard_state = "normal"
        row.delivery_guard_reason = None
        row.delivery_guard_terminal_count = 0
        row.delivery_guard_error_count = 0
        row.delivery_guard_error_rate = 0.0
        row.delivery_guard_triggered_at = None
        row.delivery_guard_last_error_at = None
        row.warmup_task_id = None
        row.warmup_status = "idle"
        row.warmup_sent_count = 0
        row.warmup_error_count = 0
        row.warmup_started_at = None
        row.warmup_completed_at = None
        if row.status == "disabled_by_guard":
            row.status = "active"
            row.last_error = None
        session.execute(
            delete(DeliveryChannelOutcome).where(DeliveryChannelOutcome.connection_id == connection_id)
        )
        row.updated_at = _now()
        snapshot = guard_snapshot(row)
    if warmup_task_id:
        from src.workers.task_queue import request_cancel

        try:
            request_cancel(warmup_task_id)
        except Exception:
            logger.exception(
                "connection_warmup_cancel_failed",
                connection_id=connection_id,
                task_id=warmup_task_id,
            )
    return snapshot


def reserve_channel_send_slot(
    connection_id: str,
    *,
    allow_warmup: bool = False,
) -> float:
    """Reserve one shared send slot; return seconds until a retry, or 0."""
    now = _now()
    hour_start = now - timedelta(hours=1)
    day_start = now - timedelta(days=1)
    with session_scope() as session:
        row = session.execute(
            select(SmtpMailbox).where(SmtpMailbox.id == connection_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise LookupError("Delivery connection not found.")
        if row.delivery_guard_state == "disabled" or row.status == "disabled_by_guard":
            raise DeliveryChannelDisabled(
                row.delivery_guard_reason or "Канал отключён из-за ошибок доставки."
            )
        if row.delivery_guard_state == "warmup" and not allow_warmup:
            raise DeliveryChannelDisabled(
                "Обычная отправка приостановлена до завершения прогрева подключения."
            )

        session.execute(
            delete(DeliveryChannelSendSlot).where(
                DeliveryChannelSendSlot.connection_id == connection_id,
                DeliveryChannelSendSlot.reserved_at < now - timedelta(days=2),
            )
        )
        hour_limit = _effective_hour_limit(row)
        day_limit = max(0, int(row.max_per_day or 0))
        retry_at: list[datetime] = []
        if hour_limit > 0:
            hour_count = int(
                session.scalar(
                    select(func.count(DeliveryChannelSendSlot.id)).where(
                        DeliveryChannelSendSlot.connection_id == connection_id,
                        DeliveryChannelSendSlot.reserved_at >= hour_start,
                    )
                )
                or 0
            )
            if hour_count >= hour_limit:
                oldest = session.scalar(
                    select(DeliveryChannelSendSlot.reserved_at)
                    .where(
                        DeliveryChannelSendSlot.connection_id == connection_id,
                        DeliveryChannelSendSlot.reserved_at >= hour_start,
                    )
                    .order_by(DeliveryChannelSendSlot.reserved_at.asc())
                    .limit(1)
                )
                if oldest:
                    retry_at.append(oldest + timedelta(hours=1))
        if day_limit > 0:
            day_count = int(
                session.scalar(
                    select(func.count(DeliveryChannelSendSlot.id)).where(
                        DeliveryChannelSendSlot.connection_id == connection_id,
                        DeliveryChannelSendSlot.reserved_at >= day_start,
                    )
                )
                or 0
            )
            if day_count >= day_limit:
                oldest = session.scalar(
                    select(DeliveryChannelSendSlot.reserved_at)
                    .where(
                        DeliveryChannelSendSlot.connection_id == connection_id,
                        DeliveryChannelSendSlot.reserved_at >= day_start,
                    )
                    .order_by(DeliveryChannelSendSlot.reserved_at.asc())
                    .limit(1)
                )
                if oldest:
                    retry_at.append(oldest + timedelta(days=1))
        if retry_at:
            return max(0.1, max((item - now).total_seconds() for item in retry_at))

        session.add(
            DeliveryChannelSendSlot(
                id=str(uuid4()),
                connection_id=connection_id,
                reserved_at=now,
            )
        )
        return 0.0


def wait_for_channel_send_slot(
    connection_id: str,
    *,
    allow_warmup: bool = False,
) -> None:
    """Wait in bounded intervals until a slot is available across all workers."""
    while True:
        wait_seconds = reserve_channel_send_slot(
            connection_id,
            allow_warmup=allow_warmup,
        )
        if wait_seconds <= 0:
            return
        time.sleep(min(wait_seconds, 60.0))
