from __future__ import annotations

import unittest

from src.campaigns.chain_template_utils import (
    CHAIN_BUTTONS_MARKER,
    TEXT_CHAIN_BUTTONS_MARKER,
    build_chain_buttons_preview_html,
    has_chain_button_placeholder,
    inject_chain_buttons,
    strip_chain_button_placeholder,
    substitute_chain_buttons_preview,
)


class ChainTemplateUtilsTests(unittest.TestCase):
    def test_has_chain_button_placeholder(self) -> None:
        html = f'<div {CHAIN_BUTTONS_MARKER} style="text-align:center"><span>stub</span></div>'
        self.assertTrue(has_chain_button_placeholder(html))
        self.assertFalse(has_chain_button_placeholder("<p>no placeholder</p>"))

    def test_inject_replaces_placeholder_in_place(self) -> None:
        html = (
            "<p>Hello</p>"
            f'<div {CHAIN_BUTTONS_MARKER} style="text-align:center;padding:12px 0">'
            "<span>stub</span></div><p>Footer</p>"
        )
        text = f"Hello\n\n{TEXT_CHAIN_BUTTONS_MARKER}\nFooter"
        buttons = [("Получить КП", "token-1"), ("Отказаться", "token-2")]

        result_html, result_text = inject_chain_buttons(html, text, buttons)

        self.assertIn("Hello", result_html)
        self.assertIn("Footer", result_html)
        self.assertNotIn(CHAIN_BUTTONS_MARKER, result_html)
        self.assertIn("/chain/branch/token-1", result_html)
        self.assertIn("Получить КП", result_html)
        self.assertIn("/chain/branch/token-2", result_html)
        self.assertIn("text-align:center", result_html)
        self.assertIn("padding:12px 0", result_html)
        self.assertLess(result_html.index("Hello"), result_html.index("Получить КП"))
        self.assertLess(result_html.index("Получить КП"), result_html.index("Footer"))
        self.assertIn("/chain/branch/token-1", result_text)
        self.assertIn("Получить КП:", result_text)
        self.assertNotIn(TEXT_CHAIN_BUTTONS_MARKER, result_text)

    def test_inject_fallback_appends_when_no_placeholder(self) -> None:
        html = "<p>Hello</p>"
        text = "Hello"
        buttons = [("Далее", "token-1")]

        result_html, result_text = inject_chain_buttons(html, text, buttons)

        self.assertTrue(result_html.startswith("<p>Hello</p>"))
        self.assertIn("/chain/branch/token-1", result_html)
        self.assertIn("margin-top:16px", result_html)
        self.assertIn("Далее:", result_text)
        self.assertIn("/chain/branch/token-1", result_text)

    def test_inject_no_buttons_returns_unchanged(self) -> None:
        html = f'<div {CHAIN_BUTTONS_MARKER}>stub</div>'
        text = "plain"
        result_html, result_text = inject_chain_buttons(html, text, [])
        self.assertEqual(result_html, html)
        self.assertEqual(result_text, text)

    def test_strip_chain_button_placeholder(self) -> None:
        html = (
            "<p>Hello</p>"
            f'<div {CHAIN_BUTTONS_MARKER} style="text-align:center"><span>stub</span></div>'
            "<p>Footer</p>"
        )
        stripped = strip_chain_button_placeholder(html)
        self.assertEqual(stripped, "<p>Hello</p><p>Footer</p>")

    def test_substitute_chain_buttons_preview(self) -> None:
        html = f'<div {CHAIN_BUTTONS_MARKER} style="text-align:left;padding:8px 0">stub</div>'
        preview = substitute_chain_buttons_preview(html)
        self.assertIn("Вариант 1", preview)
        self.assertIn("Вариант 2", preview)
        self.assertIn("text-align:left", preview)
        self.assertNotIn(CHAIN_BUTTONS_MARKER, preview)

    def test_build_chain_buttons_preview_html(self) -> None:
        preview = build_chain_buttons_preview_html(wrapper_style="text-align:right;padding:4px 0")
        self.assertIn("text-align:right", preview)
        self.assertIn("padding:4px 0", preview)


if __name__ == "__main__":
    unittest.main()
