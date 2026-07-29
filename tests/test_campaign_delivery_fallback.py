from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from src.campaigns.delivery_fallback_service import process_campaign_delivery_fallbacks
from src.campaigns.recipient_email_service import RECIPIENT_STRATEGY_PRIMARY_THEN_FALLBACK
from src.generator.delivery.email_validation import EmailValidationResult


class CampaignDeliveryFallbackTests(unittest.TestCase):
    @patch("src.campaigns.delivery_fallback_service.resend_campaign_recipient_email", return_value="rusender:fallback-1")
    @patch("src.campaigns.delivery_fallback_service.session_scope")
    @patch("src.campaigns.delivery_fallback_service.resolve_delivery_email", return_value=("backup@example.com", []))
    @patch("src.campaigns.delivery_fallback_service._campaign_sent_items")
    @patch("src.generator.delivery.sender_agent._latest_delivery_events_by_row_recipient")
    def test_process_campaign_delivery_fallbacks_resends_next_candidate(
        self,
        latest_events_mock,
        sent_items_mock,
        resolve_mock,
        session_scope_mock,
        resend_mock,
    ) -> None:
        recipient_id = 42
        sent_items_mock.return_value = [
            {
                "campaign_id": "camp-1",
                "recipient_id": recipient_id,
                "row_id": str(recipient_id),
                "recipient": "person@bad.invalid",
                "recipient_strategy": RECIPIENT_STRATEGY_PRIMARY_THEN_FALLBACK,
                "fallback_candidates": ["backup@example.com"],
                "subject": "Subject",
                "send_mode": "email",
                "provider_message_id": "task-1",
            }
        ]
        latest_events_mock.return_value = {
            (str(recipient_id), "person@bad.invalid"): {
                "provider_status": "hard_bounced",
                "event_type": "external_mail.hard_bounced",
            }
        }

        session = session_scope_mock.return_value.__enter__.return_value
        camp = unittest.mock.Mock()
        camp.owner_username = "owner"
        camp.job_id = "job-1"
        camp.mail_subject = "Subject"
        camp.name = "Campaign"
        camp.connection_ids = []
        recipient = unittest.mock.Mock()
        recipient.extra = {"tried_emails": ["person@bad.invalid"]}

        def _get(model: object, key: object) -> object | None:
            if key == "camp-1":
                return camp
            if key == recipient_id:
                return recipient
            return None

        session.get.side_effect = _get

        with patch(
            "src.campaigns.connection_service.pick_available_connection",
            return_value=unittest.mock.Mock(id="conn-1", transport="rusender"),
        ):
            result = process_campaign_delivery_fallbacks(job_id="job-1", provider="rusender", campaign_id="camp-1")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["dispatched_rows"]), 1)
        self.assertEqual(result["dispatched_rows"][0]["next_recipient"], "backup@example.com")
        resend_mock.assert_called_once()
        resolve_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
