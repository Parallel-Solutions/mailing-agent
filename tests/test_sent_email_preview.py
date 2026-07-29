"""Tests for per-recipient sent email preview."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.campaigns import sent_email_preview_service
from src.infra.models import Campaign, CampaignRecipient


class SentEmailPreviewTests(unittest.TestCase):
    def test_single_template_preview(self) -> None:
        camp = Campaign(
            id="camp-1",
            owner_username="alice",
            job_id="job-abc",
            name="Test",
            mail_subject="Hello {{company}}",
            send_scenario="single",
        )
        recipient = CampaignRecipient(
            id=7,
            campaign_id="camp-1",
            row_index=0,
            company="ACME",
            contact_name="Ivan",
            email="ivan@acme.test",
        )

        class FakeSession:
            def get(self, model, key):
                if model is Campaign and key == "camp-1":
                    return camp
                if model is CampaignRecipient and key == 7:
                    return recipient
                return None

            def scalar(self, *_args, **_kwargs):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch("src.campaigns.sent_email_preview_service.session_scope") as scope, \
             patch(
                 "src.campaigns.sent_email_preview_service._render_single_template_preview",
                 return_value={
                     "node_id": "main",
                     "node_name": "Основное письмо",
                     "subject": "Subj ACME",
                     "body_html": "<p>Hi ACME</p>",
                     "body_text": "Hi ACME",
                     "email_template_id": None,
                     "issues": [],
                     "attachments": [],
                 },
             ), \
             patch(
                 "src.campaigns.sent_email_preview_service._sent_at_for_recipient",
                 return_value="2026-05-01T10:00:00",
             ):
            scope.return_value.__enter__.return_value = FakeSession()
            result = sent_email_preview_service.preview_sent_email_for_recipient(
                "camp-1",
                "alice",
                recipient_id=7,
            )

        self.assertEqual(result["recipient"]["id"], 7)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["subject"], "Subj ACME")
        self.assertIn("ACME", result["items"][0]["body_html"])


if __name__ == "__main__":
    unittest.main()
