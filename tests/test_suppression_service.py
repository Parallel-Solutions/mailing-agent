from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from src.campaigns.audience_service import create_audience, replace_members
from src.campaigns.service import create_campaign, replace_recipients
from src.campaigns.suppression_service import apply_global_email_suppression
from src.generator.delivery.suppression_store import is_suppressed
from src.infra.db import session_scope
from src.infra.models import AudienceMember, CampaignRecipient
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class SuppressionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"sup{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Suppression test"})
        self.campaign_id = self.campaign["id"]
        self.email = "blocked@example.com"
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[
                {
                    "company": "Org A",
                    "contact_name": "Contact",
                    "email": self.email,
                }
            ],
        )
        audience = create_audience(self.username, "Base")
        replace_members(
            audience["id"],
            self.username,
            members=[
                {
                    "company": "Org A",
                    "contact_name": "Contact",
                    "email": self.email,
                }
            ],
        )

    def test_apply_global_email_suppression_blocks_and_excludes(self) -> None:
        result = apply_global_email_suppression(
            self.email,
            reason="unsubscribe",
            source="test",
            job_id=self.campaign_id,
        )
        self.assertTrue(result["suppressed"])
        self.assertGreaterEqual(result["campaigns_excluded"], 1)
        self.assertGreaterEqual(result["audiences_excluded"], 1)

        suppressed, reason = is_suppressed(self.email)
        self.assertTrue(suppressed)
        self.assertEqual(reason, "unsubscribe")

        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
            member = session.scalar(select(AudienceMember))
            self.assertIsNotNone(recipient)
            self.assertIsNotNone(member)
            assert recipient is not None
            assert member is not None
            self.assertTrue(recipient.excluded)
            self.assertEqual(recipient.send_status, "skipped")
            self.assertTrue(member.excluded)

    def test_replace_recipients_marks_suppressed_as_excluded(self) -> None:
        apply_global_email_suppression(self.email, reason="unsubscribe", source="test")
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[
                {
                    "company": "Org B",
                    "contact_name": "Other",
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
            self.assertTrue(recipient.excluded)


if __name__ == "__main__":
    unittest.main()
