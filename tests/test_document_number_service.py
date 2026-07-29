from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from src.campaigns import company_service
from src.campaigns.document_number_service import (
    allocate_document_number,
    build_allocation_key,
    peek_document_number,
    resolve_campaign_company_id,
)
from src.campaigns.document_type_service import detect_document_type_key
from src.campaigns.substitution_context import build_substitution_context
from src.campaigns.substitution_engine import render_text, template_has_identifier_placeholder
from src.infra.models import Campaign, CampaignRecipient
from tests.bootstrap import bootstrap_test_runtime


class DocumentTypeServiceTests(unittest.TestCase):
    def test_detects_kp_from_content(self) -> None:
        key = detect_document_type_key(
            template_name="Шаблон",
            text="КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ\n№ {{ид}}-КП",
        )
        self.assertEqual(key, "kp")

    def test_detects_contract_from_name_fallback(self) -> None:
        key = detect_document_type_key(template_name="Договор оказания услуг", text="")
        self.assertEqual(key, "contract")

    def test_slugifies_unknown_template_name(self) -> None:
        key = detect_document_type_key(template_name="Акт выполненных работ", text="")
        self.assertEqual(key, "akt-vypolnennyh-rabot")


class DocumentNumberServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.company = company_service.create_company(name="Sender Org")
        self.other_company = company_service.create_company(name="Other Org")

    def test_increments_from_one_for_same_company_and_type(self) -> None:
        first = allocate_document_number(
            company_id=self.company["id"],
            document_type_key="kp",
            allocation_key=build_allocation_key(campaign_id="camp-1", recipient_id=1, template_id="tpl-1"),
        )
        second = allocate_document_number(
            company_id=self.company["id"],
            document_type_key="kp",
            allocation_key=build_allocation_key(campaign_id="camp-1", recipient_id=2, template_id="tpl-1"),
        )
        self.assertEqual(first, 1)
        self.assertEqual(second, 2)

    def test_isolates_counters_by_company_and_type(self) -> None:
        kp_number = allocate_document_number(
            company_id=self.company["id"],
            document_type_key="kp",
            allocation_key=build_allocation_key(campaign_id="camp-1", recipient_id=1, template_id="tpl-kp"),
        )
        contract_number = allocate_document_number(
            company_id=self.company["id"],
            document_type_key="contract",
            allocation_key=build_allocation_key(campaign_id="camp-1", recipient_id=1, template_id="tpl-contract"),
        )
        other_company_number = allocate_document_number(
            company_id=self.other_company["id"],
            document_type_key="kp",
            allocation_key=build_allocation_key(campaign_id="camp-2", recipient_id=1, template_id="tpl-kp"),
        )
        self.assertEqual(kp_number, 1)
        self.assertEqual(contract_number, 1)
        self.assertEqual(other_company_number, 1)

    def test_reuses_number_for_same_allocation_key(self) -> None:
        allocation_key = build_allocation_key(campaign_id="camp-1", recipient_id=10, template_id="tpl-1")
        first = allocate_document_number(
            company_id=self.company["id"],
            document_type_key="kp",
            allocation_key=allocation_key,
        )
        second = allocate_document_number(
            company_id=self.company["id"],
            document_type_key="kp",
            allocation_key=allocation_key,
        )
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(
            peek_document_number(company_id=self.company["id"], document_type_key="kp"),
            2,
        )

    def test_peek_returns_next_without_allocating(self) -> None:
        self.assertEqual(peek_document_number(company_id=self.company["id"], document_type_key="kp"), 1)
        allocate_document_number(
            company_id=self.company["id"],
            document_type_key="kp",
            allocation_key=build_allocation_key(campaign_id="camp-1", recipient_id=1, template_id="tpl-1"),
        )
        self.assertEqual(peek_document_number(company_id=self.company["id"], document_type_key="kp"), 2)


class DocumentIdSubstitutionTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.company = company_service.create_company(name="Sender Org")
        self.recipient = CampaignRecipient(
            id=1,
            campaign_id="camp-1",
            row_index=5,
            company="Administration A",
            contact_name="Ivanov I.I.",
            email="a@example.com",
            region="Test Region",
            extra={"adm_name": "Administration A"},
        )
        self.campaign = Campaign(
            id="camp-1",
            owner_username="owner",
            name="Test campaign",
            work_type="stp_mo",
            draft_payload={"company_id": self.company["id"]},
        )

    def test_template_has_identifier_placeholder(self) -> None:
        self.assertTrue(template_has_identifier_placeholder("№ {{ид}}-КП от {{current_date}}"))
        self.assertFalse(template_has_identifier_placeholder("Hello {{company}}"))

    def test_renders_cyrillic_identifier_with_allocated_number(self) -> None:
        with patch("src.campaigns.substitution_context.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = datetime(2026, 7, 21, 12, 0, 0)
            mocked_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            context = build_substitution_context(
                recipient=self.recipient,
                campaign=self.campaign,
                template_id="tpl-kp",
                template_name="Коммерческое предложение",
                template_text="КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ\n№ {{ид}}-КП от {{current_date}}",
                allocate_document_id=True,
            )
        rendered = render_text("№ {{ид}}-КП от {{current_date}}", context)
        self.assertEqual(rendered, "№ 1-КП от 21.07.2026")
        self.assertEqual(context["DOCUMENT_ID"], "1")
        self.assertEqual(context["ид"], "1")

    def test_resolve_campaign_company_id_from_draft(self) -> None:
        self.assertEqual(resolve_campaign_company_id(self.campaign), self.company["id"])


if __name__ == "__main__":
    unittest.main()
