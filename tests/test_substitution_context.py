from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from src.campaigns.substitution_context import build_substitution_context, _resolve_director_name
from src.campaigns.substitution_engine import (
    build_replacement_pairs,
    discover_placeholders,
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

    def test_adm_name_mapping_uses_inflected_recipient_and_normalizes_quote_spacing(self) -> None:
        raw_adm_name = (
            "\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f "
            "\u043c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u043e\u0431\u0440\u0430\u0437\u043e\u0432\u0430\u043d\u0438\u044f\""
            "\u042f\u0431\u043b\u043e\u043d\u043e\u0432\u0441\u043a\u043e\u0435 \u0433\u043e\u0440\u043e\u0434\u0441\u043a\u043e\u0435 \u043f\u043e\u0441\u0435\u043b\u0435\u043d\u0438\u0435\""
        )
        recipient = CampaignRecipient(
            id=9,
            campaign_id="camp-1",
            row_index=1,
            company=raw_adm_name,
            email="test@example.com",
            extra={
                "adm_name": raw_adm_name,
                "mun_name": "\u042f\u0431\u043b\u043e\u043d\u043e\u0432\u0441\u043a\u043e\u0435 \u0433\u043e\u0440\u043e\u0434\u0441\u043a\u043e\u0435 \u043f\u043e\u0441\u0435\u043b\u0435\u043d\u0438\u0435",
            },
        )
        campaign = Campaign(
            id="camp-1",
            owner_username="owner",
            name="Recipient case campaign",
            work_type="stp_mo",
            draft_payload={"variable_mapping": {"ADM_NAME": "adm_name"}},
        )

        context = build_substitution_context(
            recipient=recipient,
            campaign=campaign,
            template_text="{{ADM_NAME}}",
        )
        rendered = render_text("{{ADM_NAME}}", context)

        self.assertEqual(context["ADM_NAME_RAW"], raw_adm_name)
        self.assertEqual(context["ADM_NAME"], context["ADM_NAME_1"])
        self.assertEqual(context["ADM"], context["ADM_NAME_1"])
        self.assertTrue(
            rendered.startswith("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0438 ")
        )
        education = "\u043e\u0431\u0440\u0430\u0437\u043e\u0432\u0430\u043d\u0438\u044f"
        self.assertIn(f'{education} "', rendered)
        self.assertNotIn(f'{education}"', rendered)

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

    def test_builds_legacy_name_placeholders_from_fio(self) -> None:
        recipient = CampaignRecipient(
            id=2,
            campaign_id="camp-1",
            row_index=1,
            company="ООО Техностар",
            contact_name="Федорова Ирина Александровна",
            email="test@example.com",
            region="Test Region",
            extra={},
        )
        context = build_substitution_context(recipient=recipient, campaign=self.campaign)
        self.assertEqual(context["CONTACT_FIRST_NAME"], "Ирина")
        self.assertEqual(context["CONTACT_PATRONYMIC"], "Александровна")
        self.assertEqual(context["CONTACT_SURNAME"], "Федорова")
        self.assertEqual(context["Имя"], "Ирина")
        self.assertEqual(context["Отчество"], "Александровна")
        self.assertEqual(context["Фамилия"], "Федорова")
        self.assertEqual(context["CONTACT_FIRST_PATRONYMIC"], "Ирина Александровна")
        self.assertEqual(context["Имя Отчество"], "Ирина Александровна")

    def test_render_text_replaces_combined_name_placeholder(self) -> None:
        recipient = CampaignRecipient(
            id=5,
            campaign_id="camp-1",
            row_index=4,
            company="ООО Техностар",
            contact_name="Федорова Ирина Александровна",
            email="test@example.com",
            region="Test Region",
            extra={},
        )
        context = build_substitution_context(
            recipient=recipient,
            campaign=self.campaign,
            template_text="Здравствуйте, {{Имя Отчество}}!",
        )
        rendered = render_text("Здравствуйте, {{Имя Отчество}}!", context)
        self.assertEqual(rendered, "Здравствуйте, Ирина Александровна!")

    def test_render_text_replaces_name_placeholder_aliases(self) -> None:
        recipient = CampaignRecipient(
            id=6,
            campaign_id="camp-1",
            row_index=5,
            company="ООО Техностар",
            contact_name="Федорова Ирина Александровна",
            email="test@example.com",
            region="Test Region",
            extra={},
        )
        context = build_substitution_context(recipient=recipient, campaign=self.campaign)
        self.assertEqual(render_text("Здравствуйте, {{ИО}}!", context), "Здравствуйте, Ирина Александровна!")
        self.assertEqual(
            render_text("Здравствуйте, {{им. отч.}}!", context),
            "Здравствуйте, Ирина Александровна!",
        )

    def test_combined_name_placeholder_without_patronymic(self) -> None:
        recipient = CampaignRecipient(
            id=7,
            campaign_id="camp-1",
            row_index=6,
            company="ООО Техностар",
            contact_name="Федорова Ирина",
            email="test@example.com",
            region="Test Region",
            extra={},
        )
        context = build_substitution_context(recipient=recipient, campaign=self.campaign)
        self.assertEqual(context["CONTACT_FIRST_PATRONYMIC"], "Ирина")
        rendered = render_text("Здравствуйте, {{Имя Отчество}}!", context)
        self.assertEqual(rendered, "Здравствуйте, Ирина!")

    def test_combined_name_placeholder_for_name_and_patronymic_only(self) -> None:
        recipient = CampaignRecipient(
            id=8,
            campaign_id="camp-1",
            row_index=7,
            company="ООО Техностар",
            contact_name="Ирина Александровна",
            email="test@example.com",
            region="Test Region",
            extra={},
        )
        context = build_substitution_context(recipient=recipient, campaign=self.campaign)
        self.assertEqual(context["CONTACT_FIRST_PATRONYMIC"], "Ирина Александровна")
        rendered = render_text("Здравствуйте, {{Имя Отчество}}!", context)
        self.assertEqual(rendered, "Здравствуйте, Ирина Александровна!")

    def test_render_text_replaces_legacy_name_placeholders(self) -> None:
        recipient = CampaignRecipient(
            id=3,
            campaign_id="camp-1",
            row_index=2,
            company="ООО Техностар",
            contact_name="Федорова Ирина Александровна",
            email="test@example.com",
            region="Test Region",
            extra={},
        )
        context = build_substitution_context(
            recipient=recipient,
            campaign=self.campaign,
            template_text="Здравствуйте, {{Имя}} {{Отчество}}!",
        )
        rendered = render_text("Здравствуйте, {{Имя}} {{Отчество}}!", context)
        self.assertEqual(rendered, "Здравствуйте, Ирина Александровна!")

    def test_legacy_name_fallback_for_single_word_contact(self) -> None:
        recipient = CampaignRecipient(
            id=4,
            campaign_id="camp-1",
            row_index=3,
            company="ООО Техностар",
            contact_name="Петров",
            email="test@example.com",
            region="Test Region",
            extra={},
        )
        context = build_substitution_context(recipient=recipient, campaign=self.campaign)
        self.assertEqual(context["Имя"], "Петров")
        rendered = render_text("Здравствуйте, {{Имя}}!", context)
        self.assertEqual(rendered, "Здравствуйте, Петров!")


class SubstitutionEngineTests(unittest.TestCase):
    def test_braced_token_is_not_discovered_again_as_bare(self) -> None:
        placeholders = discover_placeholders("Дата: {{current_date}}")
        self.assertEqual(
            [(item.token, item.kind) for item in placeholders],
            [("{{current_date}}", "brace")],
        )

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

    def test_render_text_keeps_generic_territory_phrase_lowercase_inside_sentence(self) -> None:
        context = {
            "MUN_R_NAME_1": "\u043c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u043e\u043a\u0440\u0443\u0433\u0430",
        }

        rendered = render_text("project {{MUN_R_NAME_1}}", context)

        self.assertEqual(rendered, "project \u043c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u043e\u043a\u0440\u0443\u0433\u0430")

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

    def test_render_mun_name_at_sentence_start_is_capitalized(self) -> None:
        context = {
            "MUN_NAME": "Энемское городское поселение",
            "mun_name": "Энемское городское поселение",
        }
        rendered = render_text("{{mun_name}} предлагает выполнить работы.", context)
        self.assertEqual(rendered, "Энемское городское поселение предлагает выполнить работы.")

    def test_render_mun_name_mid_sentence_keeps_proper_name_capitalized(self) -> None:
        context = {
            "MUN_NAME": "Энемское городское поселение",
            "mun_name": "Энемское городское поселение",
        }
        rendered = render_text("Работы выполняются для {{mun_name}}.", context)
        self.assertTrue(rendered.startswith("Работы выполняются для "))
        self.assertTrue(rendered.endswith("."))
        self.assertIn("городское поселение", rendered)
        self.assertEqual(rendered.split(" \u0434\u043b\u044f ", 1)[1][0], "\u042d")

    def test_render_mun_name_normalizes_all_caps_source(self) -> None:
        context = {
            "MUN_NAME": "ЭНЕМСКОЕ ГОРОДСКОЕ ПОСЕЛЕНИЕ",
            "mun_name": "ЭНЕМСКОЕ ГОРОДСКОЕ ПОСЕЛЕНИЕ",
        }
        rendered = render_text("{{mun_name}}.", context)
        self.assertEqual(rendered, "Энемское городское поселение.")

    def test_render_district_adm_name_after_territory_uses_geo_case(self) -> None:
        from src.campaigns.substitution_context import _normalize_territory_context_values, _stringify_context
        from src.generator.generation.transforms import build_document_context

        row = {
            "ID": 1,
            "SUB_RF": "орловская область",
            "MUN_R_NAME": "дмитровский район",
            "MUN_NAME": "",
            "ADM_NAME": (
                "администрация муниципального образования "
                "дмитровского района орловской области"
            ),
            "HEAD_FIO": "Мураева Валентина Егоровна",
        }
        context = _stringify_context(build_document_context(row, outgoing_number=101))
        _normalize_territory_context_values(context)
        rendered = render_text(
            "подготовили проект коммерческого предложения на разработку СТП "
            "для территории {{ADM_NAME_1}}.",
            context,
        )
        self.assertIn(
            "для территории администрации Дмитровского муниципального района.",
            rendered,
        )

    def test_render_adm_name_at_sentence_start_keeps_leading_capital(self) -> None:
        context = {
            "ADM_NAME_1": "администрации муниципального образования Дмитровского района",
        }
        rendered = render_text("{{ADM_NAME_1}}", context)
        self.assertEqual(
            rendered,
            "Администрации муниципального образования Дмитровского района",
        )

    def test_render_company_after_for_uses_canonical_admin_genitive(self) -> None:
        context = {
            "company": "Администрация Дятьковского района",
            "ADM_NAME_1": "администрации Дятьковского муниципального района",
        }

        rendered = render_text(
            "Разработка Генплана и ПЗЗ для {{company}} от ООО «Параллельные решения».",
            context,
        )

        self.assertEqual(
            rendered,
            (
                "Разработка Генплана и ПЗЗ для администрации "
                "Дятьковского муниципального района от ООО «Параллельные решения»."
            ),
        )

    def test_render_regular_company_after_for_stays_unchanged(self) -> None:
        context = {
            "company": "ООО «Ромашка»",
            "ADM_NAME_1": "администрации муниципального района",
        }

        rendered = render_text("Предложение для {{company}}.", context)

        self.assertEqual(rendered, "Предложение для ООО «Ромашка».")

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
