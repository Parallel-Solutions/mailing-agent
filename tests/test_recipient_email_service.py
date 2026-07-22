from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import select

from src.campaigns.recipient_email_service import (
    normalize_import_emails,
    parse_email_candidates,
    resolve_delivery_email,
    validate_email_field,
)
from src.campaigns.service import create_campaign, replace_recipients
from src.generator.delivery.email_validation import EmailValidationResult
from src.infra.db import session_scope
from src.infra.models import CampaignRecipient
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class RecipientEmailServiceTests(unittest.TestCase):
    def test_parse_email_candidates_splits_comma_and_semicolon(self) -> None:
        candidates = parse_email_candidates(
            "one@example.com, two@example.com; three@example.com",
            "four@example.com",
        )
        self.assertEqual(
            candidates,
            [
                "one@example.com",
                "two@example.com",
                "three@example.com",
                "four@example.com",
            ],
        )

    def test_normalize_import_emails_splits_primary_and_fallback(self) -> None:
        normalized = normalize_import_emails(
            {
                "email": "glbuh@neopak.ru, tstender@neopak.ru",
                "email_fallback": "",
            }
        )
        self.assertEqual(normalized["email"], "glbuh@neopak.ru")
        self.assertEqual(normalized["email_fallback"], "tstender@neopak.ru")

    def test_validate_email_field_accepts_comma_separated_values(self) -> None:
        self.assertEqual(
            validate_email_field("bad-email, good@example.com"),
            "valid",
        )
        self.assertEqual(validate_email_field("bad-email, also-bad"), "invalid")

    def test_resolve_delivery_email_skips_invalid_and_uses_next(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        username = f"res{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Resolve email"})
        replace_recipients(
            campaign["id"],
            username,
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
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"])
            )
            self.assertIsNotNone(recipient)
            assert recipient is not None

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
                "src.campaigns.recipient_email_service.validate_email_address",
                side_effect=lambda email, **kwargs: fake_result(email, email == "backup@example.com"),
            ):
                delivery_email, attempts = resolve_delivery_email(recipient)

        self.assertEqual(delivery_email, "backup@example.com")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["recipient"], "person@bad.invalid")


class CampaignRecipientsImportTests(unittest.TestCase):
    def test_replace_recipients_accepts_zgyswi_like_comma_emails(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        username = f"imp{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Import comma emails"})
        result = replace_recipients(
            campaign["id"],
            username,
            recipients=[
                {
                    "company": 'ООО "Неопак"',
                    "contact_name": "User",
                    "email": "glbuh@neopak.ru, tstender@neopak.ru",
                }
            ],
        )
        self.assertEqual(result["invalid"], 0)
        self.assertEqual(result["total"], 1)
        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"])
            )
            self.assertIsNotNone(recipient)
            assert recipient is not None
            self.assertEqual(recipient.email, "glbuh@neopak.ru")
            self.assertEqual(recipient.email_fallback, "tstender@neopak.ru")
            self.assertEqual(recipient.validation_status, "valid")
            self.assertFalse(recipient.excluded)


if __name__ == "__main__":
    unittest.main()
