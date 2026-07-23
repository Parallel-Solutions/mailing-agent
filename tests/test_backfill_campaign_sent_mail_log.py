"""Tests for delivery_attempts → sent_mail_log backfill."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy import select

from scripts.migrate.backfill_campaign_sent_mail_log import backfill_campaign_sent_mail_log
from src.campaigns.service import create_campaign, replace_recipients
from src.generator.delivery.manager_stats import StatsFilters, build_manager_dashboard, invalidate_stats_cache
from src.infra.db import session_scope
from src.infra.models import DeliveryAttempt
from src.jobs.job_docs import append_event, read_sent_mail_log
from tests.bootstrap import bootstrap_test_runtime


class BackfillCampaignSentMailLogTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        invalidate_stats_cache()
        self.username = "backfill-stats-user"
        from src.security.user_store import create_user

        create_user(self.username, "pass-123")

    def _seed_campaign_with_attempts(self, *, sent_attempts: int, existing_logs: int) -> tuple[str, str]:
        campaign = create_campaign(self.username, {"name": "Backfill stats", "transport": "rusender"})
        campaign_id = campaign["id"]
        job_id = campaign["job_id"]
        replace_recipients(
            campaign_id,
            self.username,
            [
                {
                    "company": f"Company {idx}",
                    "email": f"user{idx}@example.com",
                    "row_index": idx,
                }
                for idx in range(sent_attempts)
            ],
        )

        with session_scope() as session:
            from src.infra.models import CampaignRecipient

            recipients = list(
                session.scalars(
                    select(CampaignRecipient)
                    .where(CampaignRecipient.campaign_id == campaign_id)
                    .order_by(CampaignRecipient.row_index)
                ).all()
            )
            for idx, recipient in enumerate(recipients, start=1):
                session.add(
                    DeliveryAttempt(
                        campaign_id=campaign_id,
                        recipient_id=int(recipient.id),
                        batch_id=None,
                        attempt_number=1,
                        status="sent",
                        provider_message_id=f"msg-{idx}",
                        delivery_email=str(recipient.email),
                        idempotency_key=f"{campaign_id}:{recipient.id}:1",
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                )
            session.flush()

        for idx in range(existing_logs):
            append_event(
                job_id,
                "sent_mail_log",
                {
                    "row_id": str(recipients[idx].id),
                    "recipient": recipients[idx].email,
                    "email": recipients[idx].email,
                    "transport": "rusender",
                    "status": "sent",
                    "campaign_id": campaign_id,
                    "recipient_id": recipients[idx].id,
                    "provider_message_id": f"msg-{idx + 1}",
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        return campaign_id, job_id

    def test_backfill_adds_missing_rows_and_is_idempotent(self) -> None:
        campaign_id, job_id = self._seed_campaign_with_attempts(sent_attempts=3, existing_logs=1)

        first = backfill_campaign_sent_mail_log(campaign_id=campaign_id)
        self.assertEqual(first["written"], 2)
        self.assertEqual(len(read_sent_mail_log(job_id)), 3)

        second = backfill_campaign_sent_mail_log(campaign_id=campaign_id)
        self.assertEqual(second["written"], 0)
        self.assertEqual(second["skipped_existing"], 3)
        self.assertEqual(len(read_sent_mail_log(job_id)), 3)

    def test_backfilled_rows_visible_in_manager_dashboard(self) -> None:
        campaign_id, job_id = self._seed_campaign_with_attempts(sent_attempts=2, existing_logs=0)
        backfill_campaign_sent_mail_log(campaign_id=campaign_id)
        invalidate_stats_cache(job_id)

        result = build_manager_dashboard(StatsFilters(job_ids=(job_id,)))
        self.assertGreaterEqual(result["summary"]["sent"], 2)
        self.assertFalse(result["empty"])


if __name__ == "__main__":
    unittest.main()
