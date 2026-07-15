#!/usr/bin/env python3
"""Backfill suppression list from provider webhook JSONL files."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.generator.delivery.mailopost_events import load_mailopost_events, mailopost_events_path
from src.generator.delivery.rusender_events import load_rusender_events, rusender_events_path
from src.generator.delivery.suppression_store import upsert_from_provider_event
from src.generator.delivery.unisender_go_events import load_unisender_go_events, unisender_go_events_path
from src.jobs.job_docs import list_job_ids_with_sent_mail


def _backfill_records(records: list[dict], *, source: str, job_id: str | None) -> int:
    count = 0
    for record in records:
        recipient = str(record.get("recipient") or "")
        provider_status = str(record.get("provider_status") or record.get("event_type") or "")
        if upsert_from_provider_event(
            recipient=recipient,
            provider_status=provider_status,
            source=source,
            job_id=job_id,
        ):
            count += 1
    return count


def backfill_all_jobs() -> dict[str, int]:
    totals = {"rusender": 0, "mailopost": 0, "unisender_go": 0, "jobs": 0}
    job_ids = list_job_ids_with_sent_mail()
    for job_id in job_ids:
        totals["jobs"] += 1
        totals["rusender"] += _backfill_records(load_rusender_events(job_id), source="backfill_rusender", job_id=job_id)
        totals["mailopost"] += _backfill_records(load_mailopost_events(job_id), source="backfill_mailopost", job_id=job_id)
        totals["unisender_go"] += _backfill_records(
            load_unisender_go_events(job_id),
            source="backfill_unisender_go",
            job_id=job_id,
        )
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill suppression entries from webhook JSONL files.")
    parser.add_argument("--job-id", default="", help="Optional single job id")
    args = parser.parse_args()
    if args.job_id:
        job_id = str(args.job_id).strip()
        result = {
            "rusender": _backfill_records(load_rusender_events(job_id), source="backfill_rusender", job_id=job_id),
            "mailopost": _backfill_records(load_mailopost_events(job_id), source="backfill_mailopost", job_id=job_id),
            "unisender_go": _backfill_records(
                load_unisender_go_events(job_id),
                source="backfill_unisender_go",
                job_id=job_id,
            ),
        }
    else:
        result = backfill_all_jobs()
    print(result)


if __name__ == "__main__":
    main()
