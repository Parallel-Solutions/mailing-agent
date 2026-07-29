"""Inspect or repair inconsistent CampaignFlow lifecycle state.

Dry-run is the default. Use --repair only after reviewing the JSON report.
This command never enqueues delivery tasks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", help="Inspect only one campaign UUID.")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Repair safe counter/status mismatches. Without this flag the command is read-only.",
    )
    parser.add_argument(
        "--include-healthy",
        action="store_true",
        help="Include campaigns without detected anomalies in the report.",
    )
    return parser


def _compact_report(report: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(report.get("metrics") or {})
    metrics.pop("recipient_status_counts", None)
    return {
        "campaign_id": report.get("campaign_id"),
        "status": report.get("status"),
        "target_status": report.get("target_status"),
        "anomalies": list(report.get("anomalies") or []),
        "active_batches": int(report.get("active_batches") or 0),
        "active_tasks": int(report.get("active_tasks") or 0),
        "metrics": metrics,
    }


def main() -> int:
    args = _parser().parse_args()

    from sqlalchemy import select

    from src.campaigns.state import inspect_campaign_state, reconcile_campaign_state
    from src.infra.db import session_scope
    from src.infra.models import Campaign

    reports: list[dict[str, Any]] = []
    with session_scope() as session:
        statement = select(Campaign).order_by(Campaign.updated_at.desc())
        if args.campaign_id:
            statement = statement.where(Campaign.id == args.campaign_id)
        if args.repair:
            statement = statement.with_for_update()
        campaigns = session.scalars(statement).all()
        for campaign in campaigns:
            report = (
                reconcile_campaign_state(
                    session,
                    campaign,
                    repair=True,
                    actor="campaign_state_reconciler",
                )
                if args.repair
                else inspect_campaign_state(session, campaign)
            )
            if args.include_healthy or report.get("anomalies"):
                reports.append(_compact_report(report))

    output = {
        "mode": "repair" if args.repair else "dry-run",
        "campaigns_scanned": len(campaigns),
        "campaigns_reported": len(reports),
        "reports": reports,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
