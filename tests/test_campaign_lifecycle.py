from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.campaigns.state import (
    CampaignStateConflict,
    inspect_campaign_state,
    reconcile_inactive_campaigns,
    recipient_metrics,
    transition_campaign_status,
)
from src.infra.db import session_scope
from src.infra.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatusEvent,
    DeliveryAttempt,
)
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class CampaignLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"life{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        app = FastAPI()
        app.include_router(
            create_v1_router(check_auth=lambda: Principal(self.username, "t1", "user"))
        )
        self.client = TestClient(app)

    def _create_campaign(self) -> str:
        response = self.client.post("/api/v1/campaigns", json={"name": "Lifecycle"})
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["result"]["id"])

    def test_metrics_separate_processing_success_and_attempt_errors(self) -> None:
        campaign_id = self._create_campaign()
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            assert campaign is not None
            campaign.total_count = 4
            campaign.sent_count = 2
            campaign.error_count = 1
            recipients: list[CampaignRecipient] = []
            for index, status in enumerate(("sent", "in_chain", "skipped", "failed")):
                recipient = CampaignRecipient(
                    campaign_id=campaign_id,
                    row_index=index,
                    email=f"recipient-{index}@example.com",
                    send_status=status,
                )
                session.add(recipient)
                recipients.append(recipient)
            session.flush()
            for attempt_number, status in ((1, "failed"), (2, "sent")):
                session.add(
                    DeliveryAttempt(
                        campaign_id=campaign_id,
                        recipient_id=int(recipients[0].id),
                        attempt_number=attempt_number,
                        status=status,
                        idempotency_key=f"{campaign_id}:{recipients[0].id}:{attempt_number}",
                    )
                )
            session.flush()
            metrics = recipient_metrics(session, campaign)

        self.assertEqual(metrics["processed_count"], 4)
        self.assertEqual(metrics["success_count"], 2)
        self.assertEqual(metrics["skipped_count"], 1)
        self.assertEqual(metrics["failed_recipient_count"], 1)
        self.assertEqual(metrics["attempt_count"], 2)
        self.assertEqual(metrics["attempt_error_count"], 1)
        self.assertEqual(metrics["progress"], 100.0)
        self.assertEqual(metrics["success_rate"], 50.0)

    def test_reconciler_repairs_running_campaign_with_no_active_work(self) -> None:
        campaign_id = self._create_campaign()
        completed_at = datetime(2026, 7, 24, 13, 4, 59, tzinfo=timezone.utc)
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            assert campaign is not None
            campaign.status = "running"
            campaign.total_count = 3
            campaign.sent_count = 1
            campaign.error_count = 4
            campaign.completed_at = completed_at
            for index, status in enumerate(("sent", "skipped", "failed")):
                session.add(
                    CampaignRecipient(
                        campaign_id=campaign_id,
                        row_index=index,
                        email=f"repair-{index}@example.com",
                        send_status=status,
                    )
                )
            session.flush()

            dry_run = inspect_campaign_state(session, campaign)
            self.assertEqual(dry_run["target_status"], "completed_with_errors")
            self.assertIn("active_status_without_active_work", dry_run["anomalies"])

            repaired = reconcile_inactive_campaigns(session, repair=True)
            self.assertEqual(len(repaired), 1)
            self.assertEqual(repaired[0]["status"], "completed_with_errors")
            self.assertEqual(campaign.completed_at, completed_at)
            self.assertEqual(campaign.sent_count, 1)

        with session_scope() as session:
            events = session.scalars(
                select(CampaignStatusEvent).where(
                    CampaignStatusEvent.campaign_id == campaign_id
                )
            ).all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].from_status, "running")
            self.assertEqual(events[0].to_status, "completed_with_errors")

    def test_terminal_campaign_rejects_operational_actions(self) -> None:
        campaign_id = self._create_campaign()
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            assert campaign is not None
            campaign.status = "completed_with_errors"
            campaign.total_count = 1
            campaign.completed_at = datetime.now(timezone.utc)
            session.add(
                CampaignRecipient(
                    campaign_id=campaign_id,
                    row_index=0,
                    email="failed@example.com",
                    send_status="failed",
                )
            )

        loaded = self.client.get(f"/api/v1/campaigns/{campaign_id}")
        self.assertEqual(loaded.status_code, 200, loaded.text)
        payload = loaded.json()["result"]
        self.assertEqual(payload["progress"], 100.0)
        self.assertEqual(payload["failed_recipient_count"], 1)
        self.assertNotIn("pause", payload["allowed_actions"])
        self.assertNotIn("cancel", payload["allowed_actions"])

        for action in ("launch", "pause", "resume", "cancel"):
            response = self.client.post(f"/api/v1/campaigns/{campaign_id}/{action}")
            self.assertEqual(response.status_code, 409, (action, response.text))

    def test_resume_requires_unfinished_batches(self) -> None:
        campaign_id = self._create_campaign()
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            assert campaign is not None
            campaign.status = "paused"
            campaign.total_count = 1
            campaign.completed_at = None
            session.add(
                CampaignRecipient(
                    campaign_id=campaign_id,
                    row_index=0,
                    email="pending@example.com",
                    send_status="pending",
                )
            )

        response = self.client.post(f"/api/v1/campaigns/{campaign_id}/resume")
        self.assertEqual(response.status_code, 409, response.text)

    def test_transition_matrix_blocks_terminal_reopen(self) -> None:
        campaign_id = self._create_campaign()
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            assert campaign is not None
            campaign.status = "completed"
            with self.assertRaises(CampaignStateConflict):
                transition_campaign_status(
                    session,
                    campaign,
                    "running",
                    reason="invalid_reopen",
                )


if __name__ == "__main__":
    unittest.main()
