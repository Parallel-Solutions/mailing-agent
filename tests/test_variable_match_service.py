from __future__ import annotations

import unittest
import uuid

from sqlalchemy import select

from src.campaigns.service import extract_recipient_columns, parse_recipients_csv, parse_recipients_xlsx
from src.campaigns.template_service import create_template
from src.campaigns.variable_match_service import (
    _heuristic_mapping,
    collect_template_variables,
    render_template_text,
    resolve_recipient_value,
    save_variable_mapping,
    substitution_validation_errors,
    substitution_validation_issues,
)
from tests.bootstrap import bootstrap_test_runtime


class VariableMatchServiceTests(unittest.TestCase):
    def test_collect_template_variables_from_email_chain_nodes(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.chain_service import empty_chain
        from src.campaigns.service import create_campaign
        from src.infra.db import session_scope
        from src.infra.models import Campaign
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Chain vars"})
        email = create_template(
            username,
            name="Chain email",
            template_type="email",
            body_html="<p>Dear {{contact_name}} from {{ADM_NAME}}</p>",
        )
        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = email["id"]

        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            assert camp is not None
            draft = dict(camp.draft_payload or {})
            draft["email_chain"] = chain
            camp.draft_payload = draft
            session.flush()

            variables = collect_template_variables(camp)

        names = {item["name"] for item in variables}
        self.assertIn("ADM_NAME", names)
        self.assertIn("contact_name", names)

    def test_collect_template_variables_from_linked_email_chain_id(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.chain_service import create_chain, empty_chain, save_chain
        from src.campaigns.service import create_campaign, update_campaign
        from src.infra.db import session_scope
        from src.infra.models import Campaign
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Linked chain vars"})
        email = create_template(
            username,
            name="Linked chain email",
            template_type="email",
            body_html="<p>Dear {{contact_name}} from {{ADM_NAME}}</p>",
        )
        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = email["id"]
        created_chain = create_chain(username, name="Shared chain")
        save_chain(created_chain["id"], username, chain)
        update_campaign(
            campaign["id"],
            username,
            {"email_chain_id": created_chain["id"], "send_scenario": "email_chain"},
        )

        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            assert camp is not None
            session.expunge(camp)

        variables = collect_template_variables(camp)
        names = {item["name"] for item in variables}
        self.assertIn("ADM_NAME", names)
        self.assertIn("contact_name", names)

    def test_parse_recipients_csv_keeps_extra_columns(self) -> None:
        csv_body = "company,contact_name,email,mun_name,custom_field\nA,Contact,a@example.com,Municipality,Value\n"
        rows, _columns = parse_recipients_csv(csv_body.encode("utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "A")
        self.assertIn("mun_name", rows[0]["extra"])
        self.assertEqual(rows[0]["extra"]["custom_field"], "Value")
        columns = extract_recipient_columns(rows)
        self.assertIn("mun_name", columns)
        self.assertIn("custom_field", columns)

    def test_parse_recipients_xlsx_keeps_extra_columns(self) -> None:
        from io import BytesIO

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Label1", "Label2", "Label3", "Label4", "Label5"])
        ws.append(["ADM_NAME", "HEAD_FIO", "EMAIL_OSN", "SUB_RF", "MUN_NAME"])
        ws.append(["Administration A", "Ivanov I.I.", "a@example.com", "Region", "Municipality"])
        buffer = BytesIO()
        wb.save(buffer)

        rows, columns = parse_recipients_xlsx(buffer.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Administration A")
        self.assertEqual(rows[0]["contact_name"], "Ivanov I.I.")
        self.assertEqual(rows[0]["email"], "a@example.com")
        self.assertIn("mun_name", rows[0]["extra"])
        self.assertIn("adm_name", columns)
        self.assertIn("head_fio", columns)

    def test_heuristic_mapping_matches_mo_columns(self) -> None:
        template_variables = [
            {"name": "ADM_NAME", "label": "ADM_NAME", "source": "recipient"},
            {"name": "company", "label": "company", "source": "recipient"},
        ]
        recipient_columns = ["company", "contact_name", "email", "adm_name", "head_fio"]
        mapping = _heuristic_mapping(template_variables, recipient_columns)
        self.assertEqual(mapping.get("ADM_NAME"), "adm_name")
        self.assertEqual(mapping.get("company"), "company")

    def test_render_template_text_replaces_current_date(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign, CampaignRecipient
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Date test"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"])
            )
            assert camp is not None and recipient is not None
            session.expunge(camp)
            session.expunge(recipient)

        rendered = render_template_text(
            "Срок действия до {{current_date}}",
            recipient=recipient,
            campaign=camp,
        )
        self.assertNotIn("{{current_date}}", rendered)
        self.assertRegex(rendered, r"\d{2}\.\d{2}\.\d{4}")

    def test_substitution_validation_errors_for_unmapped_variable(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.chain_service import empty_chain
        from src.campaigns.chain_preview_service import preview_chain_for_campaign
        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Validation test"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        email = create_template(
            username,
            name="Broken email",
            template_type="email",
            body_html="<p>Hello {{unknown_custom_var}}</p>",
        )
        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = email["id"]
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            assert camp is not None
            draft = dict(camp.draft_payload or {})
            draft["email_chain"] = chain
            draft["mapping_confirmed"] = True
            draft["variable_mapping"] = {}
            camp.draft_payload = draft
            camp.send_scenario = "email_chain"
            session.flush()
            session.expunge(camp)

        review_errors = substitution_validation_errors(camp)
        self.assertFalse(any("unknown_custom_var" in error for error in review_errors))

        preview = preview_chain_for_campaign(campaign["id"], username)
        issues = preview["items"][0]["issues"]
        self.assertTrue(any(issue.get("kind") in {"artifact", "unresolved"} for issue in issues))
        self.assertTrue(
            any("unknown_custom_var" in str(issue.get("token") or issue.get("fragment") or "") for issue in issues)
        )

    def test_substitution_validation_errors_for_malformed_triple_brace(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.chain_service import empty_chain
        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Malformed brace test"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        email = create_template(
            username,
            name="Triple brace email",
            template_type="email",
            body_html="<p>Работы {{{Вид_работ}}} для {{company}}</p>",
        )
        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = email["id"]
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            assert camp is not None
            draft = dict(camp.draft_payload or {})
            draft["email_chain"] = chain
            draft["mapping_confirmed"] = True
            draft["variable_mapping"] = {"company": "company"}
            camp.draft_payload = draft
            camp.send_scenario = "email_chain"
            session.flush()
            session.expunge(camp)

        issues = substitution_validation_issues(camp)
        self.assertFalse(any(issue.get("kind") == "malformed" for issue in issues))
        self.assertFalse(any("Вид_работ" in str(issue.get("token")) for issue in issues))
        errors = substitution_validation_errors(camp)
        self.assertFalse(any("некоррект" in error.lower() for error in errors))

    def test_save_variable_mapping_accepts_literal_prefix(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.chain_service import empty_chain
        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Literal mapping"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        email = create_template(
            username,
            name="Literal email",
            template_type="email",
            body_html="<p>Hello {{CUSTOM_TITLE}}</p>",
        )
        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = email["id"]
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            assert camp is not None
            draft = dict(camp.draft_payload or {})
            draft["email_chain"] = chain
            camp.draft_payload = draft
            camp.send_scenario = "email_chain"
            session.flush()

        saved = save_variable_mapping(
            campaign["id"],
            username,
            {"CUSTOM_TITLE": "=ООО Рога и копыта"},
        )
        self.assertTrue(saved["mapping_confirmed"])
        self.assertEqual(saved["variable_mapping"]["CUSTOM_TITLE"], "=ООО Рога и копыта")

    def test_save_variable_mapping_treats_unknown_text_as_literal(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.chain_service import empty_chain
        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Raw literal mapping"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        email = create_template(
            username,
            name="Raw literal email",
            template_type="email",
            body_html="<p>Hello {{CUSTOM_TITLE}}</p>",
        )
        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = email["id"]
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            assert camp is not None
            draft = dict(camp.draft_payload or {})
            draft["email_chain"] = chain
            camp.draft_payload = draft
            camp.send_scenario = "email_chain"
            session.flush()

        saved = save_variable_mapping(
            campaign["id"],
            username,
            {"CUSTOM_TITLE": "Фиксированный текст"},
        )
        self.assertTrue(saved["mapping_confirmed"])
        self.assertEqual(saved["variable_mapping"]["CUSTOM_TITLE"], "=Фиксированный текст")

    def test_resolve_recipient_value_returns_literal_for_all_recipients(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import CampaignRecipient
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Literal resolve"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[
                {"company": "A", "contact_name": "Ivan", "email": "a@example.com"},
                {"company": "B", "contact_name": "Petr", "email": "b@example.com"},
            ],
        )
        with session_scope() as session:
            recipients = list(
                session.scalars(
                    select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"])
                )
            )
            self.assertEqual(len(recipients), 2)
            for recipient in recipients:
                self.assertEqual(
                    resolve_recipient_value(recipient, "=ООО Рога и копыта"),
                    "ООО Рога и копыта",
                )

    def test_render_template_text_uses_literal_mapping(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign, CampaignRecipient
        from src.security.user_store import create_user

        username = f"var{uuid.uuid4().hex[:8]}"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Literal render"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"])
            )
            assert camp is not None and recipient is not None
            session.expunge(camp)
            session.expunge(recipient)

        rendered = render_template_text(
            "Hello {{CUSTOM_TITLE}}",
            recipient=recipient,
            campaign=camp,
            variable_mapping={"CUSTOM_TITLE": "=ООО Рога и копыта"},
        )
        self.assertIn("ООО Рога и копыта", rendered)
        self.assertNotIn("{{CUSTOM_TITLE}}", rendered)


if __name__ == "__main__":
    unittest.main()
