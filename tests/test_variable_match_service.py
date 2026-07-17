from __future__ import annotations

import unittest
import uuid

from src.campaigns.service import extract_recipient_columns, parse_recipients_csv, parse_recipients_xlsx
from src.campaigns.template_service import create_template
from src.campaigns.variable_match_service import _heuristic_mapping, collect_template_variables
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


if __name__ == "__main__":
    unittest.main()
