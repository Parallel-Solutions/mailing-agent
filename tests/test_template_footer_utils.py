from __future__ import annotations

import unittest

from src.campaigns.template_footer_utils import strip_email_metadata_footer
from src.campaigns.template_starters import EMAIL_STARTERS


class TemplateFooterUtilsTests(unittest.TestCase):
    def test_strips_email_materials_muted_footer(self) -> None:
        starter = next(item for item in EMAIL_STARTERS if item["id"] == "email-materials")
        html = str(starter["body_html"])
        self.assertNotIn("Контакт: {{email}}", html)
        self.assertNotIn("{{campaign_name}}", html)

        sample = (
            '<div><p style="margin:0 0 16px">Hello</p>'
            '<p style="margin:0;font-size:13px;color:#6c757d">'
            "Контакт: {{email}} · Регион: {{region}} · {{campaign_name}}"
            "</p></div>"
        )
        cleaned = strip_email_metadata_footer(sample)
        self.assertNotIn("Контакт:", cleaned)
        self.assertIn("Hello", cleaned)

    def test_strips_visual_campaign_name_footer_row(self) -> None:
        sample = (
            "<table><tr><td>Body</td></tr>"
            '<tr><td style="background:#f4f6f5;padding:20px;text-align:center">'
            "© {{campaign_name}} · "
            '<a href="#">Отписаться</a>'
            "</td></tr></table>"
        )
        cleaned = strip_email_metadata_footer(sample)
        self.assertNotIn("© {{campaign_name}}", cleaned)
        self.assertIn("Body", cleaned)

    def test_preserves_contact_name_in_body(self) -> None:
        sample = "<p>Контакт: {{contact_name}} из {{company}}</p>"
        cleaned = strip_email_metadata_footer(sample)
        self.assertEqual(sample, cleaned)

    def test_strips_plain_text_footer_line(self) -> None:
        sample = (
            "Здравствуйте!\n\n"
            "Основной текст.\n"
            "Контакт: {{email}} · Регион: {{region}} · {{campaign_name}}"
        )
        cleaned = strip_email_metadata_footer(sample)
        self.assertIn("Основной текст.", cleaned)
        self.assertNotIn("{{campaign_name}}", cleaned)


if __name__ == "__main__":
    unittest.main()
