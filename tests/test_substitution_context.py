from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from src.campaigns.substitution_context import build_substitution_context, _resolve_director_name
from src.campaigns.substitution_engine import (
    build_replacement_pairs,
    find_unresolved_placeholders,
    render_text,
)
from src.infra.models import Campaign, CampaignRecipient
from src.campaigns import company_service
from tests.bootstrap import bootstrap_test_runtime


class SubstitutionContextTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.recipient = CampaignRecipient(
            id=1,
            campaign_id="camp-1",
            row_index=5,
            company="Administration A",
            contact_name="Ivanov I.I.",
            email="a@example.com",
            region="Test Region",
            extra={"adm_name": "Administration A", "sub_rf": "Test Region", "mun_r_name": "District"},
        )
        self.campaign = Campaign(
            id="camp-1",
            owner_username="owner",
            name="Test campaign",
            work_type="stp_mo",
            draft_payload={"variable_mapping": {"ADM_NAME": "adm_name"}},
        )

    def test_builds_system_variables_and_aliases(self) -> None:
        with patch("src.campaigns.substitution_context.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = datetime(2026, 7, 21, 12, 0, 0)
            mocked_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            context = build_substitution_context(recipient=self.recipient, campaign=self.campaign, outgoing_number=5)

        self.assertEqual(context["DATE"], "21.07.2026")
        self.assertEqual(context["current_date"], "21.07.2026")
        self.assertEqual(context["VALID_UNTIL"], (datetime(2026, 7, 21) + timedelta(days=30)).strftime("%d.%m.%Y"))
        self.assertEqual(context["campaign_name"], "Test campaign")
        self.assertEqual(context["OUTGOING_NUMBER"], "5")

    def test_mapping_does_not_override_inflected_scope_fragment(self) -> None:
        context = build_substitution_context(recipient=self.recipient, campaign=self.campaign)
        scope = context.get("MUN_R_SCOPE_FRAGMENT", "")
        self.assertTrue(scope)
        self.assertNotEqual(scope, "District")

    def test_uses_draft_company_for_director_name(self) -> None:
        company = company_service.create_company(
            name="Sender Org",
            contact_person_name="Petrov P.P.",
        )
        campaign = Campaign(
            id="camp-2",
            owner_username="owner",
            name="Company campaign",
            work_type="stp_mo",
            draft_payload={"company_id": company["id"]},
        )
        self.assertEqual(_resolve_director_name(campaign), "Petrov P.P.")

    def test_overrides_work_title_from_company_catalog(self) -> None:
        company = company_service.create_company(name="Sender Org")
        work_type = company_service.create_company_work_type(company["id"], name="Градостроительный аудит")
        campaign = Campaign(
            id="camp-3",
            owner_username="owner",
            name="Work type campaign",
            work_type="stp_mo",
            draft_payload={
                "company_id": company["id"],
                "company_work_type_id": work_type["id"],
            },
        )
        context = build_substitution_context(recipient=self.recipient, campaign=campaign)
        self.assertEqual(context["WORK_TITLE"], "Градостроительный аудит")
        self.assertEqual(context["WORK_TITLE_1"], "Градостроительный аудит")
        self.assertEqual(context["WORK_TITLE_NOMINATIVE"], "Градостроительный аудит")
        self.assertNotIn("схемы территориального планирования", context["WORK_TITLE"].casefold())

    def test_builds_system_variables_from_draft_mapping(self) -> None:
        company = company_service.create_company(name="Sender Org")
        work_type = company_service.create_company_work_type(company["id"], name="Градостроительный аудит")
        campaign = Campaign(
            id="camp-5",
            owner_username="owner",
            name="Mapped campaign",
            work_type="stp_mo",
            draft_payload={
                "company_id": company["id"],
                "company_work_type_id": work_type["id"],
                "system_variables": {"вид работ": "WORK_TITLE"},
            },
        )
        context = build_substitution_context(
            recipient=self.recipient,
            campaign=campaign,
            template_text="Работы: {{вид работ}}",
        )
        rendered = render_text("Работы: {{вид работ}}", context)
        self.assertEqual(rendered, "Работы: Градостроительный аудит")

    def test_renders_cyrillic_work_title_alias_from_company_catalog(self) -> None:
        company = company_service.create_company(name="Sender Org")
        work_type = company_service.create_company_work_type(company["id"], name="Градостроительный аудит")
        campaign = Campaign(
            id="camp-4",
            owner_username="owner",
            name="Work type campaign",
            work_type="stp_mo",
            draft_payload={
                "company_id": company["id"],
                "company_work_type_id": work_type["id"],
            },
        )
        context = build_substitution_context(
            recipient=self.recipient,
            campaign=campaign,
            template_text="Работы: {{Вид_работ}}",
        )
        self.assertEqual(context["WORK_TITLE"], "Градостроительный аудит")
        self.assertEqual(context["Вид_работ"], "Градостроительный аудит")
        rendered = render_text("Работы: {{Вид_работ}}", context)
        self.assertEqual(rendered, "Работы: Градостроительный аудит")


class SubstitutionEngineTests(unittest.TestCase):
    def test_render_text_replaces_brace_and_bare_tokens(self) -> None:
        context = {
            "DATE": "21.07.2026",
            "current_date": "21.07.2026",
            "ADM_NAME": "Administration A",
            "MUN_R_SCOPE_FRAGMENT": "District Test Region",
        }
        text = "Until {{current_date}} for ADM_NAME in MUN_R_NAME SUB_RF"
        rendered = render_text(text, context)
        self.assertIn("21.07.2026", rendered)
        self.assertIn("Administration A", rendered)
        self.assertIn("District Test Region", rendered)
        self.assertNotIn("{{current_date}}", rendered)
        self.assertNotIn("ADM_NAME", rendered)

    def test_build_replacement_pairs_sorts_longest_first(self) -> None:
        context = {"MUN_R_SCOPE_FRAGMENT": "Scope", "MUN_R_NAME": "District", "SUB_RF": "Region"}
        text = "MUN_R_NAME SUB_RF and MUN_R_NAME"
        pairs = build_replacement_pairs(context, text)
        tokens = [token for token, _value in pairs]
        self.assertEqual(tokens[0], "MUN_R_NAME SUB_RF")

    def test_find_unresolved_placeholders(self) -> None:
        unresolved = find_unresolved_placeholders("Hello {{missing}} and ADM_NAME")
        self.assertIn("{{missing}}", unresolved)
        self.assertIn("ADM_NAME", unresolved)

    def test_find_unresolved_malformed_triple_brace(self) -> None:
        from src.campaigns.substitution_engine import discover_malformed_placeholders

        text = "разработку {{{Вид_работ}}} для"
        unresolved = find_unresolved_placeholders(text)
        self.assertIn("{{{Вид_работ}}}", unresolved)
        malformed = discover_malformed_placeholders(text)
        self.assertEqual(len(malformed), 1)
        self.assertEqual(malformed[0].kind, "malformed")
        self.assertEqual(malformed[0].name, "Вид_работ")

    def test_find_unbalanced_brace_syntax(self) -> None:
        from src.campaigns.substitution_engine import discover_broken_brace_syntax

        text = "разработку {{{Вид_работ}} для"
        broken = discover_broken_brace_syntax(text)
        self.assertTrue(any("Вид_работ" in item.name for item in broken))

    def test_find_brace_artifact_with_spaces(self) -> None:
        from src.campaigns.substitution_engine import discover_brace_artifacts, find_template_defects

        text = "на разработку {{ стп }} для территории"
        artifacts = discover_brace_artifacts(text)
        self.assertTrue(any("стп" in item.name for item in artifacts))
        defects = find_template_defects(text, source="rendered")
        self.assertTrue(any(item.kind == "artifact" for item in defects))

    def test_html_to_review_text_finds_artifact(self) -> None:
        from src.campaigns.substitution_engine import find_unresolved_placeholders, html_to_review_text

        html = "<p>на разработку {{ стп }} для</p>"
        plain = html_to_review_text(html)
        self.assertIn("{{ стп }}", plain)
        unresolved = find_unresolved_placeholders(html)
        self.assertIn("{{ стп }}", unresolved)

    def test_render_text_replaces_cyrillic_work_title_alias(self) -> None:
        context = {"WORK_TITLE": "стп", "Вид_работ": "стп"}
        rendered = render_text("разработку {{Вид_работ}} для", context)
        self.assertEqual(rendered, "разработку стп для")

    def test_render_text_substitutes_spaced_work_title_artifact(self) -> None:
        context = {"WORK_TITLE": "разработке схемы территориального планирования"}
        rendered = render_text("на разработку {{вид работ}} для территории", context)
        self.assertIn("разработке схемы территориального планирования", rendered)
        self.assertNotIn("{{вид работ}}", rendered)

    def test_render_text_leaves_unresolvable_stp_placeholder(self) -> None:
        context = {"WORK_TITLE": "разработке схемы территориального планирования"}
        rendered = render_text("на разработку {{ стп }} для территории", context)
        self.assertIn("{{ стп }}", rendered)
        self.assertNotIn("разработке схемы территориального планирования", rendered)

    def test_render_text_fixes_territory_genitive_after_company_placeholder(self) -> None:
        context = {
            "company": "Энемское городское поселение",
            "MUN_NAME": "Энемское городское поселение",
            "MUN_NAME_1": "Энемского городского поселения",
        }
        rendered = render_text(
            "на разработку СТП для территории {{company}}.",
            context,
        )
        self.assertIn("для территории Энемского городского поселения", rendered)
        self.assertNotIn("для территории Энемское городское поселение", rendered)

    def test_render_text_leaves_malformed_triple_brace_unsubstituted(self) -> None:
        context = {
            "WORK_TITLE": "разработке схемы территориального планирования муниципального образования",
            "Вид_работ": "разработке схемы территориального планирования муниципального образования",
            "MUN_NAME": "Энемское городское поселение",
            "MUN_NAME_1": "Энемского городского поселения",
            "mun_name": "Энемское городское поселение",
        }
        rendered = render_text(
            "разработку {{{Вид_работ}} для территории {{mun_name}}.",
            context,
        )
        self.assertIn("{", rendered)
        self.assertIn("для территории Энемского городского поселения", rendered)
        self.assertNotIn("для территории Энемское городское поселение", rendered)

    def test_renders_cyrillic_identifier_alias(self) -> None:
        context = {
            "DOCUMENT_ID": "42",
            "ид": "42",
            "DATE": "21.07.2026",
        }
        rendered = render_text("№ {{ид}} от {{DATE}}", context)
        self.assertEqual(rendered, "№ 42 от 21.07.2026")


if __name__ == "__main__":
    unittest.main()
