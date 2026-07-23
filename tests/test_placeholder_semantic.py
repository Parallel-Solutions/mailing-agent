from __future__ import annotations

import unittest
from unittest.mock import patch

from src.campaigns.placeholder_semantic import (
    reset_semantic_index,
    resolve_recipient_canonical,
    resolve_system_canonical,
    semantic_match_recipient_column,
)
from src.campaigns.substitution_ai import _heuristic_system_variable
from src.generator.generation.template_analysis import _mapping_suggestions


def _fake_embed(text: str) -> list[float]:
    normalized = str(text or "").casefold()
    buckets = {
        "work": 0.0,
        "director": 0.0,
        "adm": 0.0,
        "adm_name": 0.0,
    }
    if "вид" in normalized and "работ" in normalized:
        buckets["work"] = 1.0
    if "work_title" in normalized or "вид работ" in normalized:
        buckets["work"] = max(buckets["work"], 0.95)
    if "подписант" in normalized or "director" in normalized:
        buckets["director"] = 1.0
    if "полное название администрации" in normalized or "adm_name" in normalized:
        buckets["adm"] = 1.0
        buckets["adm_name"] = 1.0
    if normalized.strip() == "adm_name":
        buckets["adm_name"] = 1.0
    return [buckets["work"], buckets["director"], buckets["adm"], buckets["adm_name"]]


class PlaceholderSemanticTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_semantic_index()

    def tearDown(self) -> None:
        reset_semantic_index()

    def test_exact_alias_resolves_work_title_without_semantic(self) -> None:
        self.assertEqual(resolve_system_canonical("Вид_работ"), "WORK_TITLE")
        self.assertEqual(resolve_system_canonical("вид_работ"), "WORK_TITLE")
        self.assertEqual(_heuristic_system_variable("Вид_работ"), "WORK_TITLE")

    def test_exact_alias_resolves_legacy_name_placeholders(self) -> None:
        self.assertEqual(resolve_recipient_canonical("Имя"), "CONTACT_FIRST_NAME")
        self.assertEqual(resolve_recipient_canonical("Отчество"), "CONTACT_PATRONYMIC")
        self.assertEqual(resolve_recipient_canonical("Компания"), "company")

    @patch("src.campaigns.placeholder_semantic._semantic_enabled", return_value=True)
    @patch("src.campaigns.placeholder_semantic._embed_text", side_effect=_fake_embed)
    def test_semantic_resolves_director_name(self, *_mocks: object) -> None:
        reset_semantic_index()
        self.assertEqual(resolve_system_canonical("Подписант"), "DIRECTOR_NAME")

    @patch("src.campaigns.placeholder_semantic._semantic_enabled", return_value=True)
    @patch("src.campaigns.placeholder_semantic._embed_text", side_effect=_fake_embed)
    def test_semantic_recipient_column_mapping(self, *_mocks: object) -> None:
        reset_semantic_index()
        match = semantic_match_recipient_column(
            "Полное название администрации",
            ["company", "contact_name", "adm_name"],
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.canonical, "adm_name")
        self.assertGreater(match.score, 0.0)

    @patch("src.campaigns.placeholder_semantic._semantic_enabled", return_value=True)
    @patch("src.campaigns.placeholder_semantic._embed_text", side_effect=_fake_embed)
    def test_mapping_suggestions_use_semantic_match(self, *_mocks: object) -> None:
        reset_semantic_index()
        suggestions = _mapping_suggestions(
            ["Полное название администрации"],
            ["company", "adm_name"],
        )
        self.assertEqual(len(suggestions), 1)
        candidates = suggestions[0]["candidates"]
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["column"], "adm_name")
        self.assertEqual(candidates[0]["reason"], "semantic_match")

    @patch("src.campaigns.placeholder_semantic._semantic_enabled", return_value=True)
    @patch("src.campaigns.placeholder_semantic._embed_text", side_effect=_fake_embed)
    def test_resolve_recipient_canonical_by_label(self, *_mocks: object) -> None:
        reset_semantic_index()
        self.assertEqual(resolve_recipient_canonical("Полное название администрации"), "ADM_NAME")

    def test_exact_alias_beats_semantic_path(self) -> None:
        with patch("src.campaigns.placeholder_semantic._semantic_enabled", return_value=True):
            with patch("src.campaigns.placeholder_semantic._embed_text", side_effect=_fake_embed):
                reset_semantic_index()
                self.assertEqual(resolve_system_canonical("Вид_работ"), "WORK_TITLE")


if __name__ == "__main__":
    unittest.main()
