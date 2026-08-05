"""Typed, indexed storage for provider (and first-party SMTP) delivery
events — replaces the previous generic JSONB `job_events` streams
(rusender_events/mailopost_events/unisender_go_events) with a single table
that has real columns/indexes for job_id, recipient, campaign_id and
provider_status.

Callers keep passing/reading the same legacy record dict shape; this module
only changes *where* it is durably stored. See ``rusender_events.py`` /
``mailopost_events.py`` / ``unisender_go_events.py`` for the write side and
``sender_report.py`` for the read side.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infra.db import session_scope
from src.infra.models import ProviderDeliveryEvent, ProviderTaskLookup


def append_provider_event(
    *,
    source: str,
    job_id: str = "",
    campaign_id: str | None = None,
    connection_id: str | None = None,
    provider_task_id: str = "",
    recipient: str = "",
    row_id: str = "",
    event_type: str = "",
    provider_status: str = "",
    smtp_response: str = "",
    occurred_at: str = "",
    event_key: str,
    payload: dict[str, Any],
) -> bool:
    """Idempotent insert keyed on ``(source, event_key)``.

    Returns True if a new row was inserted, False if it already existed
    (duplicate webhook replay) or ``event_key`` was empty.
    """
    if not event_key:
        return False
    with session_scope() as session:
        stmt = (
            pg_insert(ProviderDeliveryEvent)
            .values(
                source=source,
                job_id=job_id or "",
                campaign_id=campaign_id or None,
                connection_id=connection_id or None,
                provider_task_id=provider_task_id or "",
                recipient=recipient or "",
                row_id=row_id or "",
                event_type=event_type or "",
                provider_status=provider_status or "",
                smtp_response=smtp_response or None,
                occurred_at=_parse_datetime(occurred_at),
                event_key=event_key,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["source", "event_key"])
            .returning(ProviderDeliveryEvent.id)
        )
        result = session.execute(stmt)
        return result.first() is not None


def load_provider_events(source: str, job_id: str | None) -> list[dict[str, Any]]:
    """Return the legacy record-dict shape (``payload``) for a job's events,
    oldest first — matching the historical ``read_jsonl`` ordering."""
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return []
    with session_scope() as session:
        rows = session.scalars(
            select(ProviderDeliveryEvent)
            .where(
                ProviderDeliveryEvent.source == source,
                ProviderDeliveryEvent.job_id == normalized_job_id,
            )
            .order_by(ProviderDeliveryEvent.id.asc())
        ).all()
        return [dict(row.payload or {}) for row in rows]


def upsert_provider_task_lookup(
    *,
    provider_task_id: str,
    job_id: str = "",
    campaign_id: str | None = None,
    connection_id: str | None = None,
    recipient: str = "",
    row_id: str = "",
) -> None:
    """Record job/campaign/recipient for a provider-accepted message at
    send time, so a later webhook can resolve it in one indexed lookup
    instead of scanning the entire sent_mail_log history."""
    task_id = str(provider_task_id or "").strip()
    if not task_id:
        return
    values = {
        "job_id": job_id or "",
        "campaign_id": campaign_id or None,
        "connection_id": connection_id or None,
        "recipient": recipient or "",
        "row_id": row_id or "",
    }
    with session_scope() as session:
        stmt = (
            pg_insert(ProviderTaskLookup)
            .values(provider_task_id=task_id, **values)
            .on_conflict_do_update(index_elements=["provider_task_id"], set_=values)
        )
        session.execute(stmt)


def lookup_provider_tasks(provider_task_ids: list[str]) -> dict[str, dict[str, str]]:
    """Fast indexed lookup for a specific batch of provider task ids.

    Callers should fall back to the historical full-scan index
    (``_load_task_job_index``/``_load_message_job_index``) for any id not
    found here — those are sends made before this table existed.
    """
    ids = sorted({str(item or "").strip() for item in provider_task_ids if str(item or "").strip()})
    if not ids:
        return {}
    with session_scope() as session:
        rows = session.scalars(
            select(ProviderTaskLookup).where(ProviderTaskLookup.provider_task_id.in_(ids))
        ).all()
        return {
            row.provider_task_id: {
                "job_id": row.job_id or "",
                "campaign_id": row.campaign_id or "",
                "connection_id": row.connection_id or "",
                "recipient": row.recipient or "",
                "row_id": row.row_id or "",
            }
            for row in rows
        }


def has_provider_events(source: str, job_id: str | None) -> bool:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return False
    with session_scope() as session:
        return (
            session.scalar(
                select(ProviderDeliveryEvent.id)
                .where(
                    ProviderDeliveryEvent.source == source,
                    ProviderDeliveryEvent.job_id == normalized_job_id,
                )
                .limit(1)
            )
            is not None
        )


def _parse_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
