#!/usr/bin/env python3
"""Backfill missing sent_mail_log events from delivery_attempts audit trail.

CampaignFlow increments campaigns.sent_count and writes delivery_attempts on
success, but sent_mail_log (statistics source of truth) may be missing when
append_event failed or ran on an older build. This script merges missing rows
idempotently without skipping jobs that already have partial logs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.campaigns.recipient_email_service import build_campaign_sent_mail_log_record
from src.generator.delivery.manager_stats import invalidate_stats_cache
from src.generator.delivery.sender_agent import _safe_text
from src.infra.db import init_db, session_scope
from src.infra.models import Campaign, CampaignRecipient, DeliveryAttempt
from src.jobs.job_docs import append_event, read_sent_mail_log


_SENT_STATUSES = frozenset({"sent", "delivered"})


def _sent_at_iso(value: datetime | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _load_existing_log_index(job_id: str) -> tuple[set[str], set[tuple[str, str, str]]]:
    provider_ids: set[str] = set()
    recipient_keys: set[tuple[str, str, str]] = set()
    for item in read_sent_mail_log(job_id):
        provider_id = _safe_text(item.get("provider_message_id"))
        if provider_id:
            provider_ids.add(provider_id)
        campaign_id = _safe_text(item.get("campaign_id"))
        recipient_id = _safe_text(item.get("recipient_id") or item.get("row_id"))
        email = _safe_text(item.get("recipient") or item.get("email")).lower()
        if campaign_id and recipient_id and email:
            recipient_keys.add((campaign_id, recipient_id, email))
    return provider_ids, recipient_keys


def _attempt_already_logged(
    *,
    attempt: DeliveryAttempt,
    delivery_email: str,
    provider_ids: set[str],
    recipient_keys: set[tuple[str, str, str]],
) -> bool:
    provider_id = _safe_text(attempt.provider_message_id)
    if provider_id and provider_id in provider_ids:
        return True
    key = (attempt.campaign_id, str(attempt.recipient_id), delivery_email.lower())
    return key in recipient_keys


def backfill_campaign_sent_mail_log(
    *,
    campaign_id: str | None = None,
    job_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    touched_jobs: set[str] = set()
    written = 0
    skipped_existing = 0
    skipped_no_job = 0
    skipped_no_email = 0
    errors = 0

    with session_scope() as session:
        stmt = (
            select(DeliveryAttempt, Campaign, CampaignRecipient)
            .join(Campaign, Campaign.id == DeliveryAttempt.campaign_id)
            .join(CampaignRecipient, CampaignRecipient.id == DeliveryAttempt.recipient_id)
            .where(DeliveryAttempt.status.in_(tuple(_SENT_STATUSES)))
            .order_by(DeliveryAttempt.id.asc())
        )
        if campaign_id:
            stmt = stmt.where(DeliveryAttempt.campaign_id == campaign_id)
        if job_id:
            stmt = stmt.where(Campaign.job_id == job_id)
        rows = session.execute(stmt).all()

    log_index_by_job: dict[str, tuple[set[str], set[tuple[str, str, str]]]] = {}

    for attempt, camp, recipient in rows:
        storage_job_id = _safe_text(camp.job_id)
        if not storage_job_id:
            skipped_no_job += 1
            continue

        delivery_email = _safe_text(attempt.delivery_email or recipient.email)
        if not delivery_email:
            skipped_no_email += 1
            continue

        if storage_job_id not in log_index_by_job:
            log_index_by_job[storage_job_id] = _load_existing_log_index(storage_job_id)
        provider_ids, recipient_keys = log_index_by_job[storage_job_id]

        if _attempt_already_logged(
            attempt=attempt,
            delivery_email=delivery_email,
            provider_ids=provider_ids,
            recipient_keys=recipient_keys,
        ):
            skipped_existing += 1
            continue

        record = build_campaign_sent_mail_log_record(
            campaign_id=camp.id,
            recipient_id=int(recipient.id),
            recipient=recipient,
            delivery_email=delivery_email,
            provider_message_id=_safe_text(attempt.provider_message_id),
            transport=_safe_text(camp.transport) or "smtp",
            send_mode="email",
            subject=_safe_text(camp.mail_subject),
            campaign_name=_safe_text(camp.name),
            sent_at=_sent_at_iso(attempt.updated_at or attempt.created_at),
        )
        idempotency_key = f"backfill:{attempt.campaign_id}:{attempt.recipient_id}:{attempt.attempt_number}"

        if dry_run:
            written += 1
            touched_jobs.add(storage_job_id)
            provider_id = _safe_text(attempt.provider_message_id)
            if provider_id:
                provider_ids.add(provider_id)
            recipient_keys.add((attempt.campaign_id, str(attempt.recipient_id), delivery_email.lower()))
            continue

        try:
            seq = append_event(
                storage_job_id,
                "sent_mail_log",
                record,
                idempotency_key=idempotency_key,
            )
        except Exception:
            errors += 1
            continue

        if seq is None:
            skipped_existing += 1
            continue

        written += 1
        touched_jobs.add(storage_job_id)
        provider_id = _safe_text(attempt.provider_message_id)
        if provider_id:
            provider_ids.add(provider_id)
        recipient_keys.add((attempt.campaign_id, str(attempt.recipient_id), delivery_email.lower()))

    if not dry_run and touched_jobs:
        invalidate_stats_cache()

    return {
        "dry_run": dry_run,
        "attempts_scanned": len(rows),
        "written": written,
        "skipped_existing": skipped_existing,
        "skipped_no_job": skipped_no_job,
        "skipped_no_email": skipped_no_email,
        "errors": errors,
        "jobs_touched": sorted(touched_jobs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill missing sent_mail_log rows from delivery_attempts."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report actions without writing")
    parser.add_argument("--campaign-id", default="", help="Optional single campaign id")
    parser.add_argument("--job-id", default="", help="Optional single job id")
    args = parser.parse_args(argv)
    init_db()
    result = backfill_campaign_sent_mail_log(
        campaign_id=str(args.campaign_id or "").strip() or None,
        job_id=str(args.job_id or "").strip() or None,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
