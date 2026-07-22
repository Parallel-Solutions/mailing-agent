from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import select

from src.campaigns.batch_worker import run_sender_batch
from src.campaigns.connection_service import ResolvedConnection
from src.campaigns.service import create_campaign, replace_recipients
from src.campaigns.suppression_service import apply_global_email_suppression
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignBatch, CampaignRecipient
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class BatchWorkerSuppressionTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"bw{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Batch suppression"})
        self.campaign_id = self.campaign["id"]
        self.email = "skip@example.com"
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[
                {
                    "company": "Org",
                    "contact_name": "User",
                    "email": self.email,
                }
            ],
        )
        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
            self.assertIsNotNone(recipient)
            assert recipient is not None
            self.recipient_id = int(recipient.id)
            camp = session.get(Campaign, self.campaign_id)
            assert camp is not None
            camp.status = "running"
            camp.smtp_mailbox_id = "conn-test"
            self.batch_id = str(uuid.uuid4())
            session.add(
                CampaignBatch(
                    id=self.batch_id,
                    campaign_id=self.campaign_id,
                    batch_index=0,
                    scheduled_at=datetime.now(timezone.utc),
                    size=1,
                    status="pending",
                    recipient_ids=[self.recipient_id],
                )
            )
            session.flush()

    @patch("src.campaigns.batch_worker._load_email_template", return_value=("Subject", "<p>Hi</p>", "Hi"))
    @patch("src.campaigns.batch_worker._send_delivery_message")
    @patch("src.campaigns.connection_service.resolve_connection")
    def test_run_sender_batch_skips_suppressed_recipient(self, resolve_mock, send_mock, _template_mock) -> None:
        resolve_mock.return_value = ResolvedConnection(
            id="conn-test",
            transport="smtp",
            email="sender@example.com",
            sender_name="Sender",
            secret="secret",
            api_base_url="",
        )
        apply_global_email_suppression(self.email, reason="unsubscribe", source="test")

        result = run_sender_batch(
            {
                "campaign_id": self.campaign_id,
                "batch_id": self.batch_id,
                "send_mode": "consent_request",
                "smtp_mailbox_id": "conn-test",
                "on_error": "skip",
            }
        )

        self.assertEqual(result.get("sent"), 0)
        send_mock.assert_not_called()
        with session_scope() as session:
            recipient = session.get(CampaignRecipient, self.recipient_id)
            self.assertIsNotNone(recipient)
            assert recipient is not None
            self.assertEqual(recipient.send_status, "skipped")
            self.assertTrue(recipient.excluded)


if __name__ == "__main__":
    unittest.main()
