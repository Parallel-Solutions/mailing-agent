#!/usr/bin/env python3
"""Backfill JobOwner from Campaign.owner_username for sent-mail jobs missing an owner.

Orphan jobs without a matching Campaign are left ownerless (admin-visible only).
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import Campaign
from src.jobs.access import assign_job_owner
from src.jobs.job_docs import list_job_ids_with_sent_mail, read_owner
from src.security.auth import Principal


def backfill_job_owners(*, job_id: str | None = None) -> dict[str, int]:
    if job_id:
        candidates = [job_id.strip()] if job_id.strip() else []
    else:
        candidates = list_job_ids_with_sent_mail()

    assigned = 0
    skipped_has_owner = 0
    skipped_no_campaign = 0

    for candidate in candidates:
        if not candidate:
            continue
        if read_owner(candidate):
            skipped_has_owner += 1
            continue

        with session_scope() as session:
            camp = session.scalar(select(Campaign).where(Campaign.job_id == candidate).limit(1))
            owner_username = str(camp.owner_username or "").strip() if camp is not None else ""

        if not owner_username:
            skipped_no_campaign += 1
            continue

        assign_job_owner(candidate, Principal(owner_username, "default", "user"), overwrite=False)
        assigned += 1

    return {
        "candidates": len(candidates),
        "assigned": assigned,
        "skipped_has_owner": skipped_has_owner,
        "skipped_no_campaign": skipped_no_campaign,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill JobOwner rows from Campaign.owner_username for jobs with sent mail."
    )
    parser.add_argument("--job-id", default="", help="Optional single job id")
    args = parser.parse_args()
    result = backfill_job_owners(job_id=str(args.job_id or "").strip() or None)
    print(result)


if __name__ == "__main__":
    main()
