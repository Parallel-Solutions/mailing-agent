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
from src.campaigns.service import create_campaign, parse_recipients_xlsx, replace_recipients
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

    def test_parse_recipients_xlsx_checko_export_maps_core_fields(self) -> None:
        from io import BytesIO

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Полное наименование", "Email", "Руководитель"])
        ws.append(
            [
                'Общество с ограниченной ответственностью "Неопак"',
                "glbuh@neopak.ru, tstender@neopak.ru",
                "Иванов Иван Иванович",
            ]
        )
        buffer = BytesIO()
        wb.save(buffer)

        rows, _columns = parse_recipients_xlsx(buffer.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["company"],
            'Общество с ограниченной ответственностью "Неопак"',
        )
        self.assertEqual(rows[0]["contact_name"], "Иванов Иван Иванович")
        self.assertEqual(rows[0]["email"], "glbuh@neopak.ru, tstender@neopak.ru")

    def test_replace_recipients_checko_export_splits_emails(self) -> None:
        from io import BytesIO

        from openpyxl import Workbook

        bootstrap_test_runtime(reset_db=True)
        username = f"imp{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Import Checko xlsx"})

        wb = Workbook()
        ws = wb.active
        ws.append(["Полное наименование", "Email", "Руководитель"])
        ws.append(
            [
                'Общество с ограниченной ответственностью "Неопак"',
                "glbuh@neopak.ru, tstender@neopak.ru",
                "Иванов Иван Иванович",
            ]
        )
        buffer = BytesIO()
        wb.save(buffer)
        rows, _columns = parse_recipients_xlsx(buffer.getvalue())

        result = replace_recipients(campaign["id"], username, recipients=rows)
        self.assertEqual(result["invalid"], 0)
        self.assertEqual(result["total"], 1)
        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"])
            )
            self.assertIsNotNone(recipient)
            assert recipient is not None
            self.assertEqual(
                recipient.company,
                'Общество с ограниченной ответственностью "Неопак"',
            )
            self.assertEqual(recipient.contact_name, "Иванов Иван Иванович")
            self.assertEqual(recipient.email, "glbuh@neopak.ru")
            self.assertEqual(recipient.email_fallback, "tstender@neopak.ru")
            self.assertEqual(recipient.validation_status, "valid")
            self.assertFalse(recipient.excluded)

    def test_parse_and_import_checko_zgyswi_sample_xlsx(self) -> None:
        from pathlib import Path

        from src.campaigns.variable_match_service import _heuristic_mapping

        fixture = Path(__file__).resolve().parents[1] / "fixtures/manual/recipients-checko-zgyswi-sample.xlsx"
        content = fixture.read_bytes()
        rows, columns = parse_recipients_xlsx(content)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["company"], 'Общество с ограниченной ответственностью "Неопак"')
        self.assertEqual(rows[0]["contact_name"], "Иванов Иван Иванович")
        self.assertEqual(rows[0]["email"], "glbuh@neopak.ru, tstender@neopak.ru")
        self.assertEqual(rows[0]["region"], "Карелия, республика")
        self.assertIn("сокращенное наименование", rows[0]["extra"])
        self.assertIn("полное наименование", columns)
        self.assertIn("руководитель", columns)

        bootstrap_test_runtime(reset_db=True)
        username = f"zg{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "ZGYSWI sample import"})
        result = replace_recipients(campaign["id"], username, recipients=rows, recipient_columns=columns)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["duplicates_skipped"], 1)
        self.assertEqual(result["invalid"], 0)

        with session_scope() as session:
            recipients = session.scalars(
                select(CampaignRecipient)
                .where(CampaignRecipient.campaign_id == campaign["id"])
                .order_by(CampaignRecipient.row_index)
            ).all()
            self.assertEqual(len(recipients), 2)
            self.assertEqual(recipients[0].email, "glbuh@neopak.ru")
            self.assertEqual(recipients[0].email_fallback, "tstender@neopak.ru")
            self.assertEqual(recipients[1].email, "tdsoglasie@onego.ru")
            self.assertEqual(recipients[1].email_fallback, "glavbuh@soglasie.ptz.ru")
            self.assertEqual(recipients[1].contact_name, "Петров Петр Петрович")

        mapping = _heuristic_mapping(
            [
                {"name": "ADM_NAME"},
                {"name": "HEAD_FIO"},
                {"name": "SUB_RF"},
                {"name": "MUN_NAME"},
            ],
            columns,
        )
        self.assertEqual(mapping.get("HEAD_FIO"), "руководитель")
        self.assertEqual(mapping.get("SUB_RF"), "регион")


if __name__ == "__main__":
    unittest.main()
