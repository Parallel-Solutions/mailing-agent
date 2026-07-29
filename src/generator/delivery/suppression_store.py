from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from sqlalchemy import delete, select

from src.infra.db import session_scope
from src.infra.models import SuppressionEntry
from src.utils.config import settings


SUPPRESSION_REASONS = {
    "hard_bounce",
    "soft_bounce",
    "unsubscribe",
    "spam",
    "manual",
}

WEBHOOK_STATUS_TO_REASON = {
    "hard_bounced": "hard_bounce",
    "hard_bounce": "hard_bounce",
    "email_broken": "hard_bounce",
    "err_user_unknown": "hard_bounce",
    "err_recipient_inactive": "hard_bounce",
    "soft_bounced": "soft_bounce",
    "soft_bounce": "soft_bounce",
    "err_mailbox_full": "soft_bounce",
    "unsubscribed": "unsubscribe",
    "unsubscribe": "unsubscribe",
    "spam": "spam",
    "complaint": "spam",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def reason_from_provider_status(provider_status: str) -> str | None:
    normalized = str(provider_status or "").strip().lower()
    return WEBHOOK_STATUS_TO_REASON.get(normalized)


def reason_from_delivery_response(delivery_response: str) -> str | None:
    """Classify common enhanced SMTP status codes without probing the mailbox."""
    normalized = str(delivery_response or "").strip().lower()
    if not normalized:
        return None
    hard_markers = (
        "user unknown",
        "unknown user",
        "no such user",
        "no such recipient",
        "recipient not found",
        "address rejected",
        "mailbox does not exist",
        "invalid recipient",
        "account disabled",
        "адрес не существует",
        "пользователь не найден",
    )
    soft_markers = (
        "mailbox full",
        "quota exceeded",
        "over quota",
        "temporarily unavailable",
        "try again later",
        "greylist",
        "greylisted",
        "ящик переполнен",
        "временно недоступ",
    )
    if re.search(r"(?:^|\D)5\.1\.[0-9](?:\D|$)", normalized) or any(
        marker in normalized for marker in hard_markers
    ):
        return "hard_bounce"
    if re.search(r"(?:^|\D)4\.[0-9]\.[0-9](?:\D|$)", normalized) or any(
        marker in normalized for marker in soft_markers
    ):
        return "soft_bounce"
    return None


def _expires_for_reason(reason: str) -> datetime | None:
    safe_reason = str(reason or "").strip().lower()
    if safe_reason == "soft_bounce":
        ttl_days = max(1, int(settings.suppression_soft_bounce_ttl_days or 7))
        return _now() + timedelta(days=ttl_days)
    return None


def upsert_suppression(
    email: str,
    *,
    reason: str,
    source: str = "manual",
    job_id: str | None = None,
    expires_at: datetime | None = None,
) -> bool:
    normalized = normalize_email(email)
    safe_reason = str(reason or "").strip().lower()
    if not normalized or safe_reason not in SUPPRESSION_REASONS:
        return False
    if expires_at is None:
        expires_at = _expires_for_reason(safe_reason)
    now = _now()
    with session_scope() as session:
        row = session.get(SuppressionEntry, normalized)
        if row is None:
            session.add(
                SuppressionEntry(
                    email=normalized,
                    reason=safe_reason,
                    source=str(source or "manual"),
                    job_id=str(job_id or "").strip() or None,
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            return True
        row.reason = safe_reason
        row.source = str(source or row.source or "manual")
        row.job_id = str(job_id or "").strip() or row.job_id
        row.expires_at = expires_at
        return True


def remove_suppression(email: str) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    with session_scope() as session:
        row = session.get(SuppressionEntry, normalized)
        if row is None:
            return False
        session.delete(row)
        return True


def purge_expired_suppressions() -> int:
    now = _now()
    with session_scope() as session:
        result = session.execute(
            delete(SuppressionEntry).where(
                SuppressionEntry.expires_at.is_not(None),
                SuppressionEntry.expires_at <= now,
            )
        )
        return int(result.rowcount or 0)


def is_suppressed(email: str) -> tuple[bool, str | None]:
    normalized = normalize_email(email)
    if not normalized:
        return False, None
    purge_expired_suppressions()
    with session_scope() as session:
        row = session.get(SuppressionEntry, normalized)
        if row is None:
            return False, None
        if row.expires_at is not None and row.expires_at <= _now():
            session.delete(row)
            return False, None
        return True, str(row.reason or "")


def list_suppressions(*, limit: int = 200, offset: int = 0, q: str = "") -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 200), 1000))
    safe_offset = max(0, int(offset or 0))
    query_text = normalize_email(q)
    with session_scope() as session:
        query = select(SuppressionEntry).order_by(SuppressionEntry.created_at.desc())
        if query_text:
            query = query.where(SuppressionEntry.email.contains(query_text))
        rows = session.execute(query.offset(safe_offset).limit(safe_limit)).scalars().all()
        total = len(rows)
        return {
            "items": [
                {
                    "email": row.email,
                    "reason": row.reason,
                    "source": row.source,
                    "job_id": row.job_id or "",
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                    "expires_at": row.expires_at.isoformat() if row.expires_at else "",
                }
                for row in rows
            ],
            "count": total,
            "offset": safe_offset,
            "limit": safe_limit,
        }


def upsert_from_provider_event(
    *,
    recipient: str,
    provider_status: str,
    source: str,
    job_id: str | None = None,
    delivery_response: str = "",
) -> bool:
    reason = reason_from_provider_status(provider_status) or reason_from_delivery_response(delivery_response)
    if not reason:
        return False
    if reason == "unsubscribe":
        from src.campaigns.suppression_service import apply_global_email_suppression

        result = apply_global_email_suppression(
            recipient,
            reason=reason,
            source=source,
            job_id=job_id,
        )
        return bool(result.get("suppressed"))
    return upsert_suppression(
        recipient,
        reason=reason,
        source=source,
        job_id=job_id,
    )
