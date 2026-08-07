from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import select

from src.campaigns.batch_worker import run_sender_batch
from src.campaigns.connection_service import ResolvedConnection
from src.campaigns.service import create_campaign, replace_recipients
from src.generator.delivery.email_validation import EmailValidationResult
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignBatch, CampaignRecipient
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class BatchWorkerEmailValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"bwe{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Batch email validation"})
        self.campaign_id = self.campaign["id"]
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[
                {
                    "company": "Org",
                    "contact_name": "User",
                    "email": "person@bad.invalid, backup@example.com",
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
    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="smtp:backup@example.com:1")
    @patch("src.campaigns.connection_service.pick_available_connection")
    def test_run_sender_batch_uses_second_validated_email(self, pick_mock, send_mock, _template_mock) -> None:
        pick_mock.return_value = ResolvedConnection(
            id="conn-test",
            transport="smtp",
            email="sender@example.com",
            sender_name="Sender",
            secret="secret",
            api_base_url="",
        )

        def fake_result(email: str, is_valid: bool) -> EmailValidationResult:
            return EmailValidationResult(
                email=email,
                normalized_email=email.lower(),
                domain=email.split("@", 1)[-1],
                is_valid=is_valid,
                reason_code="ok_domain" if is_valid else "domain_not_found",
                reason="" if is_valid else "Email не прошёл проверку.",
                checked_at="2026-07-22T12:00:00",
                details={"mode": "domain"},
            )

        with patch(
            "src.campaigns.email_validation_service.cached_validation_result",
            side_effect=lambda owner, email: fake_result(email, email == "backup@example.com"),
        ), patch("src.campaigns.recipient_email_service.settings.email_validation_mode", "smtpbz"):
            result = run_sender_batch(
                {
                    "campaign_id": self.campaign_id,
                    "batch_id": self.batch_id,
                    "send_mode": "email",
                    "connection_ids": ["conn-test"],
                    "on_error": "skip",
                }
            )

        self.assertEqual(result.get("sent"), 1)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["to_email"], "backup@example.com")
        with session_scope() as session:
            recipient = session.get(CampaignRecipient, self.recipient_id)
            self.assertIsNotNone(recipient)
            assert recipient is not None
            self.assertEqual(recipient.send_status, "sent")
            self.assertEqual((recipient.extra or {}).get("delivery_email"), "backup@example.com")


if __name__ == "__main__":
    unittest.main()
