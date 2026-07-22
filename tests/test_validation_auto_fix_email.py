from __future__ import annotations

import unittest
from unittest.mock import patch

from src.campaigns.validation_auto_fix_service import (
    _apply_issue_suggestions,
    _apply_placeholder_defect_fixes,
    _replace_in_field,
)


class ValidationAutoFixEmailTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("src.campaigns.validation_auto_fix_service.template_service.save_version")
        self.addCleanup(patcher.stop)
        self.mock_save = patcher.start()

    def test_replace_in_field_direct(self) -> None:
        updated, ok = _replace_in_field("Hello world", "world", "there")
        self.assertTrue(ok)
        self.assertEqual(updated, "Hello there")

    def test_replace_in_field_html_whitespace_fallback(self) -> None:
        source = "<p>текст <strong>важный</strong> .</p>"
        updated, ok = _replace_in_field(source, "важный .", "важный.")
        self.assertTrue(ok)
        self.assertIn("важный.", updated)
        self.assertNotIn(" .", updated)

    def test_apply_placeholder_defect_fixes_triple_brace(self) -> None:
        applied, skipped = _apply_placeholder_defect_fixes(
            "template-id",
            "owner",
            [],
            subject="Тема {{{WORK_TITLE}}}",
            body_html="<p>Работы по {{{WORK_TITLE}}} для клиента.</p>",
            body_text="",
        )
        self.assertFalse(skipped)
        self.assertTrue(applied)
        self.assertTrue(any(item.get("kind") == "placeholder" for item in applied))
        self.mock_save.assert_called_once()
        saved_kwargs = self.mock_save.call_args.kwargs
        self.assertIn("{{WORK_TITLE}}", saved_kwargs.get("body_html", ""))

    def test_apply_placeholder_defect_fixes_resolvable_artifact_skips_template_edit(self) -> None:
        applied, skipped = _apply_placeholder_defect_fixes(
            "template-id",
            "owner",
            [],
            subject="",
            body_html="<p>на {{вид работ}} для компании.</p>",
            body_text="",
        )
        self.assertFalse(applied)
        self.assertFalse(skipped)
        self.mock_save.assert_not_called()

    def test_apply_placeholder_defect_fixes_unresolvable_brace_artifact_skips_template_edit(self) -> None:
        applied, skipped = _apply_placeholder_defect_fixes(
            "template-id",
            "owner",
            [],
            subject="",
            body_html="<p>на {{ стp }} для компании.</p>",
            body_text="",
        )
        self.assertFalse(applied)
        self.assertFalse(skipped)
        self.mock_save.assert_not_called()

    def test_apply_placeholder_defect_fixes_resolvable_artifact_issue_skips_template_edit(self) -> None:
        applied, skipped = _apply_placeholder_defect_fixes(
            "template-id",
            "owner",
            [
                {
                    "kind": "artifact",
                    "field": "body_html",
                    "fragment": "{{вид работ}}",
                    "message": "artifact",
                }
            ],
            subject="",
            body_html="<p>на {{вид работ}} для компании.</p>",
            body_text="",
        )
        self.assertFalse(applied)
        self.assertFalse(skipped)
        self.mock_save.assert_not_called()

    def test_apply_placeholder_defect_fixes_reports_skipped_fragment(self) -> None:
        applied, skipped = _apply_placeholder_defect_fixes(
            "template-id",
            "owner",
            [
                {
                    "kind": "artifact",
                    "field": "body_html",
                    "fragment": "{missing fragment}",
                    "message": "artifact",
                }
            ],
            subject="",
            body_html="<p>чистый текст</p>",
            body_text="",
        )
        self.assertFalse(applied)
        self.assertTrue(skipped)
        self.assertIn("Не удалось определить замену", skipped[0]["message"])

    def test_apply_issue_suggestions_punctuation_in_html(self) -> None:
        applied, skipped = _apply_issue_suggestions(
            "template-id",
            "owner",
            [
                {
                    "kind": "punctuation",
                    "field": "body_html",
                    "fragment": " .",
                    "suggestion": ".",
                }
            ],
            subject="",
            body_html="<p>текст .</p>",
            body_text="",
        )
        self.assertFalse(skipped, skipped)
        self.assertTrue(applied)
        self.mock_save.assert_called()


if __name__ == "__main__":
    unittest.main()
