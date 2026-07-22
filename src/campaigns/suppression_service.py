"""Global email suppression: stop-list + exclusion from campaigns and audiences."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select

from src.generator.delivery.suppression_store import normalize_email, upsert_suppression
from src.infra.db import session_scope
from src.infra.models import AudienceMember, CampaignRecipient

_PENDING_SEND_STATUSES = frozenset({"pending"})


def apply_global_email_suppression(
    email: str,
    *,
    reason: str = "unsubscribe",
    source: str = "manual",
    job_id: str | None = None,
) -> dict[str, Any]:
    """Add email to global stop-list and exclude from all campaigns/audiences."""
    normalized = normalize_email(email)
    if not normalized:
        return {
            "suppressed": False,
            "campaigns_excluded": 0,
            "audiences_excluded": 0,
        }

    upsert_suppression(
        normalized,
        reason=reason,
        source=source,
        job_id=job_id,
    )

    campaigns_excluded = _exclude_campaign_recipients(normalized)
    audiences_excluded = _exclude_audience_members(normalized)

    return {
        "suppressed": True,
        "email": normalized,
        "campaigns_excluded": campaigns_excluded,
        "audiences_excluded": audiences_excluded,
    }


def is_email_suppressed_for_import(email: str) -> bool:
    from src.generator.delivery.suppression_store import is_suppressed

    normalized = normalize_email(email)
    if not normalized:
        return False
    suppressed, _reason = is_suppressed(normalized)
    return suppressed


def _email_match_clause(normalized: str):
    lowered = normalized.lower()
    return or_(
        func.lower(CampaignRecipient.email) == lowered,
        func.lower(CampaignRecipient.email_fallback) == lowered,
    )


def _audience_email_match_clause(normalized: str):
    lowered = normalized.lower()
    return or_(
        func.lower(AudienceMember.email) == lowered,
        func.lower(AudienceMember.email_fallback) == lowered,
    )


def _exclude_campaign_recipients(normalized: str) -> int:
    with session_scope() as session:
        rows = session.scalars(select(CampaignRecipient).where(_email_match_clause(normalized))).all()
        changed = 0
        for row in rows:
            updated = False
            if not row.excluded:
                row.excluded = True
                updated = True
            if row.send_status in _PENDING_SEND_STATUSES:
                row.send_status = "skipped"
                row.last_error = "Отписался"
                updated = True
            if updated:
                changed += 1
        session.flush()
        return changed


def _exclude_audience_members(normalized: str) -> int:
    with session_scope() as session:
        rows = session.scalars(select(AudienceMember).where(_audience_email_match_clause(normalized))).all()
        changed = 0
        for row in rows:
            if not row.excluded:
                row.excluded = True
                changed += 1
        session.flush()
        return changed
