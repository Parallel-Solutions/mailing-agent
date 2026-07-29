"""Subscribe/unsubscribe consent tracking for email chains."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.campaigns.suppression_service import apply_global_email_suppression
from src.infra.db import session_scope
from src.infra.models import CampaignChainConsentEvent

MARKETING_CONSENT_TTL_DAYS = 365
ACTION_SUBSCRIBE = "subscribe"
ACTION_UNSUBSCRIBE = "unsubscribe"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _existing_event(session: Session, token: str) -> CampaignChainConsentEvent | None:
    return session.execute(
        select(CampaignChainConsentEvent).where(CampaignChainConsentEvent.token == token)
    ).scalar_one_or_none()


def record_subscribe(
    *,
    campaign_id: str,
    recipient_id: int,
    email: str,
    node_id: str,
    edge_id: str,
    token: str,
) -> dict[str, Any]:
    now = _now()
    expires_at = now + timedelta(days=MARKETING_CONSENT_TTL_DAYS)
    with session_scope() as session:
        existing = _existing_event(session, token)
        if existing is not None:
            return {
                "action": ACTION_SUBSCRIBE,
                "created": False,
                "expires_at": existing.expires_at.isoformat() if existing.expires_at else None,
            }
        session.add(
            CampaignChainConsentEvent(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                email=str(email or "").strip().lower(),
                action=ACTION_SUBSCRIBE,
                node_id=node_id,
                edge_id=edge_id,
                token=token,
                created_at=now,
                expires_at=expires_at,
            )
        )
        session.flush()
        return {
            "action": ACTION_SUBSCRIBE,
            "created": True,
            "expires_at": expires_at.isoformat(),
        }


def record_unsubscribe(
    *,
    campaign_id: str,
    recipient_id: int,
    email: str,
    node_id: str,
    edge_id: str,
    token: str,
) -> dict[str, Any]:
    now = _now()
    normalized_email = str(email or "").strip().lower()
    with session_scope() as session:
        existing = _existing_event(session, token)
        if existing is not None:
            return {"action": ACTION_UNSUBSCRIBE, "created": False}
        session.add(
            CampaignChainConsentEvent(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                email=normalized_email,
                action=ACTION_UNSUBSCRIBE,
                node_id=node_id,
                edge_id=edge_id,
                token=token,
                created_at=now,
                expires_at=None,
            )
        )
        session.flush()
    apply_global_email_suppression(
        normalized_email,
        reason="unsubscribe",
        source="chain",
        job_id=campaign_id,
    )
    return {"action": ACTION_UNSUBSCRIBE, "created": True}


def get_consent_stats(campaign_id: str, *, session: Session | None = None) -> dict[str, Any]:
    def _query(active_session: Session) -> dict[str, Any]:
        rows = active_session.execute(
            select(
                CampaignChainConsentEvent.action,
                func.count(CampaignChainConsentEvent.id).label("total"),
            )
            .where(CampaignChainConsentEvent.campaign_id == campaign_id)
            .group_by(CampaignChainConsentEvent.action)
        ).all()
        counts = {str(r.action): int(r.total) for r in rows}
        return {
            "subscribe": {"count": counts.get(ACTION_SUBSCRIBE, 0)},
            "unsubscribe": {"count": counts.get(ACTION_UNSUBSCRIBE, 0)},
        }

    if session is not None:
        return _query(session)
    with session_scope() as scoped:
        return _query(scoped)


def has_active_marketing_consent(email: str, *, at: datetime | None = None) -> bool:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return False
    moment = at or _now()
    with session_scope() as session:
        row = session.execute(
            select(CampaignChainConsentEvent)
            .where(
                CampaignChainConsentEvent.email == normalized,
                CampaignChainConsentEvent.action == ACTION_SUBSCRIBE,
                CampaignChainConsentEvent.expires_at.is_not(None),
                CampaignChainConsentEvent.expires_at > moment,
            )
            .order_by(CampaignChainConsentEvent.expires_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row is not None
