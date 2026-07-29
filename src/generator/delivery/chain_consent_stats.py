"""Statistics views for chain subscribe/unsubscribe events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select

from src.campaigns.chain_consent_service import ACTION_SUBSCRIBE, ACTION_UNSUBSCRIBE
from src.generator.delivery.manager_stats import StatsFilters, _pct, _safe_text, _within_period
from src.generator.delivery.sender_report import _format_moscow_datetime
from src.infra.db import session_scope
from src.infra.models import (
    Campaign,
    CampaignChainConsentEvent,
    CampaignRecipient,
    SuppressionEntry,
)

SOURCE_LABELS = {
    "chain": "Кнопка в письме",
    "manual": "Вручную",
    "rusender": "RuSender",
    "mailopost": "MailoPost",
    "unisender_go": "UniSender",
    "unisender": "UniSender",
}


@dataclass(frozen=True)
class ChainConsentStatsContext:
    filters: StatsFilters
    owner_username: str
    is_admin: bool


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_allowed_campaign_ids(ctx: ChainConsentStatsContext) -> set[str] | None:
    with session_scope() as session:
        rows = session.execute(select(Campaign.id, Campaign.job_id, Campaign.owner_username)).all()
    if ctx.is_admin and not ctx.filters.job_ids:
        return None
    visible: list[tuple[str, str | None, str]] = []
    for row in rows:
        campaign_id, job_id, owner = str(row[0]), row[1], str(row[2] or "")
        if ctx.is_admin or owner == ctx.owner_username:
            visible.append((campaign_id, str(job_id or "") or None, owner))
    if not visible:
        return set()
    if len(ctx.filters.job_ids) == 1:
        selected = ctx.filters.job_ids[0]
        return {campaign_id for campaign_id, job_id, _owner in visible if campaign_id == selected or job_id == selected}
    if ctx.filters.job_ids:
        allowed_jobs = set(ctx.filters.job_ids)
        return {campaign_id for campaign_id, job_id, _owner in visible if job_id in allowed_jobs}
    return {campaign_id for campaign_id, _job_id, _owner in visible}


def _matches_query(row: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        _safe_text(row.get(key)).lower()
        for key in ("email", "organization", "contact_name", "campaign_name", "source_label")
    )
    return query in haystack


def _paginate(rows: list[dict[str, Any]], *, page: int, per_page: int) -> dict[str, Any]:
    total = len(rows)
    start = max(0, (page - 1) * per_page)
    page_rows = rows[start : start + per_page]
    return {
        "items": page_rows,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": max(1, (total + per_page - 1) // per_page),
        },
    }


def build_chain_subscribes_view(
    ctx: ChainConsentStatsContext,
    *,
    page: int = 1,
    per_page: int = 10,
) -> dict[str, Any]:
    allowed_campaign_ids = _resolve_allowed_campaign_ids(ctx)
    query_text = _safe_text(ctx.filters.q).lower()
    now = _now_utc()
    rows: list[dict[str, Any]] = []

    with session_scope() as session:
        stmt = (
            select(CampaignChainConsentEvent, Campaign, CampaignRecipient)
            .join(Campaign, Campaign.id == CampaignChainConsentEvent.campaign_id)
            .outerjoin(CampaignRecipient, CampaignRecipient.id == CampaignChainConsentEvent.recipient_id)
            .where(CampaignChainConsentEvent.action == ACTION_SUBSCRIBE)
            .order_by(CampaignChainConsentEvent.created_at.desc())
        )
        if allowed_campaign_ids is not None:
            if not allowed_campaign_ids:
                stmt = stmt.where(CampaignChainConsentEvent.campaign_id == "__none__")
            else:
                stmt = stmt.where(CampaignChainConsentEvent.campaign_id.in_(allowed_campaign_ids))
        for event, campaign, recipient in session.execute(stmt).all():
            created_at = event.created_at.isoformat() if event.created_at else ""
            if not _within_period(
                created_at,
                period_from=ctx.filters.period_from,
                period_to=ctx.filters.period_to,
            ):
                continue
            expires_at = event.expires_at.isoformat() if event.expires_at else ""
            active = bool(event.expires_at and event.expires_at > now)
            row = {
                "email": _safe_text(event.email),
                "organization": _safe_text(recipient.company if recipient else ""),
                "contact_name": _safe_text(recipient.contact_name if recipient else ""),
                "campaign_id": campaign.id,
                "campaign_name": _safe_text(campaign.name),
                "job_id": _safe_text(campaign.job_id),
                "subscribed_at": created_at,
                "subscribed_at_label": _format_moscow_datetime(created_at),
                "expires_at": expires_at,
                "expires_at_label": _format_moscow_datetime(expires_at) if expires_at else "—",
                "active": active,
            }
            if query_text and not _matches_query(row, query_text):
                continue
            rows.append(row)

    total = len(rows)
    active_count = sum(1 for row in rows if row.get("active"))
    result = _paginate(rows, page=page, per_page=per_page)
    result["summary"] = {
        "total": total,
        "active": active_count,
        "expired": max(0, total - active_count),
    }
    result["funnel"] = [
        {"id": "subscribed", "label": "Подписались", "value": total, "percent": 100.0 if total else 0.0},
        {
            "id": "active",
            "label": "Активные подписки",
            "value": active_count,
            "percent": _pct(active_count, total or 1),
        },
    ]
    return result


def _lookup_recipient_company(session, email: str) -> str:
    normalized = _safe_text(email).lower()
    if not normalized:
        return ""
    recipient = session.scalar(
        select(CampaignRecipient)
        .where(
            or_(
                func.lower(CampaignRecipient.email) == normalized,
                func.lower(CampaignRecipient.email_fallback) == normalized,
            )
        )
        .order_by(CampaignRecipient.created_at.desc())
        .limit(1)
    )
    return _safe_text(recipient.company if recipient else "")


def _lookup_chain_unsubscribe_event(session, email: str) -> CampaignChainConsentEvent | None:
    normalized = _safe_text(email).lower()
    if not normalized:
        return None
    return session.scalar(
        select(CampaignChainConsentEvent)
        .where(
            func.lower(CampaignChainConsentEvent.email) == normalized,
            CampaignChainConsentEvent.action == ACTION_UNSUBSCRIBE,
        )
        .order_by(CampaignChainConsentEvent.created_at.desc())
        .limit(1)
    )


def build_unsubscribes_view(
    ctx: ChainConsentStatsContext,
    *,
    page: int = 1,
    per_page: int = 10,
) -> dict[str, Any]:
    allowed_campaign_ids = _resolve_allowed_campaign_ids(ctx)
    allowed_jobs = set(ctx.filters.job_ids)
    query_text = _safe_text(ctx.filters.q).lower()
    rows: list[dict[str, Any]] = []

    with session_scope() as session:
        campaign_names = {
            row.id: _safe_text(row.name)
            for row in session.scalars(select(Campaign)).all()
        }
        entries = session.scalars(
            select(SuppressionEntry)
            .where(SuppressionEntry.reason == "unsubscribe")
            .order_by(SuppressionEntry.created_at.desc())
        ).all()
        for entry in entries:
            created_at = entry.created_at.isoformat() if entry.created_at else ""
            if not _within_period(
                created_at,
                period_from=ctx.filters.period_from,
                period_to=ctx.filters.period_to,
            ):
                continue
            job_or_campaign = _safe_text(entry.job_id)
            if allowed_campaign_ids is not None:
                if job_or_campaign and job_or_campaign not in allowed_campaign_ids and job_or_campaign not in allowed_jobs:
                    chain_event = _lookup_chain_unsubscribe_event(session, entry.email)
                    campaign_id = _safe_text(chain_event.campaign_id if chain_event else "")
                    if campaign_id not in allowed_campaign_ids:
                        continue
            event = _lookup_chain_unsubscribe_event(session, entry.email)
            campaign_id = _safe_text(event.campaign_id if event else job_or_campaign)
            organization = _lookup_recipient_company(session, entry.email)
            source = _safe_text(entry.source) or "manual"
            row = {
                "email": _safe_text(entry.email),
                "organization": organization,
                "source": source,
                "source_label": SOURCE_LABELS.get(source, source or "—"),
                "campaign_id": campaign_id,
                "campaign_name": campaign_names.get(campaign_id, ""),
                "job_id": job_or_campaign,
                "unsubscribed_at": created_at,
                "unsubscribed_at_label": _format_moscow_datetime(created_at),
            }
            if query_text and not _matches_query(row, query_text):
                continue
            rows.append(row)

    total = len(rows)
    by_source: dict[str, int] = {}
    for row in rows:
        key = _safe_text(row.get("source")) or "manual"
        by_source[key] = by_source.get(key, 0) + 1
    result = _paginate(rows, page=page, per_page=per_page)
    result["summary"] = {
        "total": total,
        "chain": by_source.get("chain", 0),
        "provider": sum(count for key, count in by_source.items() if key not in {"chain", "manual"}),
        "manual": by_source.get("manual", 0),
    }
    result["sources"] = [
        {
            "source": key,
            "label": SOURCE_LABELS.get(key, key),
            "count": count,
            "percent": _pct(count, total or 1),
        }
        for key, count in sorted(by_source.items(), key=lambda item: item[1], reverse=True)
    ]
    return result
