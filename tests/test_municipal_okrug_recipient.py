from __future__ import annotations

import unittest
from unittest.mock import patch

from src.generator.generation.document_builder import build_kp_replacements
from src.campaigns.substitution_engine import render_text
from src.generator.generation.transforms import build_document_context
from src.generator.inflection.ai_case_agent import (
    _apply_canonical_mo_name,
    _derive_canonical_mo_name,
    apply_case_agent_result,
)


class MunicipalOkrugRecipientTests(unittest.TestCase):
    def _row(self) -> dict:
        return {
            "ID": "1",
            "SUB_RF": "Калужская область",
            "MUN_R_NAME": "Бабынинский муниципальный округ",
            "MUN_NAME": "Бабынинский муниципальный округ",
            "ADM_NAME": "Администрация Бабынинского муниципального округа",
            "HEAD_FIO": "Иванов Иван Иванович",
        }

    def test_case_agent_keeps_district_recipient_form_for_municipal_okrug(self) -> None:
        row = self._row()
        context = build_document_context(row, 165)
        canonical_result = _derive_canonical_mo_name(row, context)

        self.assertEqual(context["DOCUMENT_ENTITY_TYPE"], "district")
        self.assertEqual(canonical_result["status"], "ok")

        context = _apply_canonical_mo_name(
            context,
            canonical_result["canonical_mo_name"],
        )
        replacements = dict(build_kp_replacements(context))

        self.assertEqual(
            context["ADM_NAME"],
            "Администрация Бабынинского муниципального округа",
        )
        self.assertEqual(
            context["ADM_NAME_1"],
            "Администрации Бабынинского муниципального округа",
        )
        self.assertEqual(
            replacements["ADM_NAME"],
            "Администрации Бабынинского муниципального округа",
        )
        self.assertEqual(replacements["ADM_NAME"], replacements["ADM_NAME_1"])

    def test_lowercase_municipality_names_are_capitalized_in_rendered_values(self) -> None:
        cases = (
            (
                "бабынинский муниципальный округ",
                "Бабынинского муниципального округа",
                "Администрации Бабынинского муниципального округа",
            ),
            (
                "дятьковский муниципальный район",
                "Дятьковского муниципального района",
                "Администрации Дятьковского муниципального района",
            ),
            (
                "одинцовский городской округ",
                "Одинцовского городского округа",
                "Администрации Одинцовского городского округа",
            ),
            (
                "яблоновское городское поселение",
                "Яблоновского городского поселения",
                'Администрации муниципального образования "Яблоновское городское поселение"',
            ),
            (
                "нийское сельское поселение",
                "Нийского сельского поселения",
                'Администрации муниципального образования "Нийское сельское поселение"',
            ),
        )

        for source_name, expected_scope, expected_recipient in cases:
            with self.subTest(source_name=source_name):
                context = build_document_context(
                    {
                        "MUN_R_NAME": source_name,
                        "MUN_NAME": source_name,
                        "ADM_NAME": "",
                        "SUB_RF": "",
                    },
                    1,
                )
                context = _apply_canonical_mo_name(context, source_name)
                replacements = dict(build_kp_replacements(context))

                self.assertEqual(context["MUN_NAME_2"], expected_scope)
                self.assertEqual(context["WORK_SCOPE_FRAGMENT"], expected_scope)
                self.assertEqual(replacements["ADM_NAME"], expected_recipient)

    def test_final_render_keeps_municipality_capitalized_inside_sentence(self) -> None:
        context = build_document_context(self._row(), 168)

        rendered = render_text("project {{MUN_NAME_2}}", context)

        self.assertEqual(
            rendered,
            "project \u0411\u0430\u0431\u044b\u043d\u0438\u043d\u0441\u043a\u043e\u0433\u043e "
            "\u043c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u043e\u0433\u043e "
            "\u043e\u043a\u0440\u0443\u0433\u0430",
        )

    @patch("src.generator.inflection.ai_case_agent.CASE_AGENT_MODE", "auto_fix")
    def test_ai_correction_cannot_restore_lowercase_geo_name(self) -> None:
        context = apply_case_agent_result(
            {"MUN_NAME_2": "Бабынинского муниципального округа"},
            {
                "enabled": True,
                "items": [
                    {
                        "field": "MUN_NAME_2",
                        "status": "fix",
                        "corrected_value": "бабынинского муниципального округа",
                        "confidence": 1.0,
                    }
                ],
            },
        )

        self.assertEqual(
            context["MUN_NAME_2"],
            "Бабынинского муниципального округа",
        )


if __name__ == "__main__":
    unittest.main()
