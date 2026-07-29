from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch

from pypdf import PdfWriter

from src.campaigns import template_preview_image_service
from src.infra.object_store import ObjectNotFoundError


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\nfake"


class TemplatePreviewImageServiceTests(unittest.TestCase):
    def test_build_email_preview_document_wraps_body(self) -> None:
        document = template_preview_image_service.build_email_preview_document("<p>Hello</p>")
        self.assertIn("<!doctype html>", document.lower())
        self.assertIn("<p>Hello</p>", document)
        self.assertIn("600px", document)

    def test_substitute_preview_sample_values(self) -> None:
        rendered = template_preview_image_service.substitute_preview_sample_values("{{company}} / {{contact_name}}")
        self.assertIn("ООО Пример", rendered)
        self.assertIn("Иван Иванов", rendered)

    @patch("src.campaigns.template_preview_image_service.render_html_to_png", return_value=PNG_SIGNATURE)
    def test_generate_starter_email_preview_png(self, _render_mock) -> None:
        starter = {
            "id": "email-greeting",
            "template_type": "email",
            "body_html": "<p>Здравствуйте, {{contact_name}}!</p>",
        }
        png = template_preview_image_service._generate_starter_preview_png(starter)
        self.assertEqual(png, PNG_SIGNATURE)
        _render_mock.assert_called_once()

    @patch("src.campaigns.template_preview_image_service._render_document_bytes_preview_png", return_value=PNG_SIGNATURE)
    def test_generate_starter_document_preview_png(self, _render_mock) -> None:
        starter = {
            "id": "document-offer",
            "template_type": "document",
            "filename": "offer-template.docx",
        }
        png = template_preview_image_service._generate_starter_preview_png(starter)
        self.assertEqual(png, PNG_SIGNATURE)
        _render_mock.assert_called_once()

    @patch("src.campaigns.template_preview_image_service.put_bytes")
    @patch("src.campaigns.template_preview_image_service.get_bytes", side_effect=ObjectNotFoundError("missing"))
    @patch("src.campaigns.template_preview_image_service._generate_starter_preview_png", return_value=PNG_SIGNATURE)
    def test_get_starter_preview_image_generates_and_caches(
        self,
        _generate_mock,
        _get_mock,
        put_mock,
    ) -> None:
        item = template_preview_image_service.get_starter_preview_image("email-greeting")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["content"], PNG_SIGNATURE)
        self.assertEqual(item["media_type"], "image/png")
        put_mock.assert_called_once()

    @patch("src.campaigns.template_preview_image_service.put_bytes")
    @patch(
        "src.campaigns.template_preview_image_service.get_bytes",
        return_value=PNG_SIGNATURE,
    )
    @patch("src.campaigns.template_preview_image_service._generate_starter_preview_png")
    def test_get_starter_preview_image_uses_cache(
        self,
        generate_mock,
        _get_mock,
        put_mock,
    ) -> None:
        item = template_preview_image_service.get_starter_preview_image("email-greeting")
        self.assertIsNotNone(item)
        generate_mock.assert_not_called()
        put_mock.assert_not_called()

    @patch("src.campaigns.template_preview_image_service.render_pdf_first_page_to_png", return_value=PNG_SIGNATURE)
    @patch("src.campaigns.template_preview_image_service.template_service.build_file_preview")
    @patch("src.campaigns.template_preview_image_service.template_service.get_template")
    def test_generate_template_document_preview_png(
        self,
        get_template_mock,
        build_file_preview_mock,
        _render_mock,
    ) -> None:
        pdf_buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(pdf_buffer)
        get_template_mock.return_value = {
            "id": "tpl-1",
            "template_type": "document",
            "version": {"id": "ver-1", "filename": "doc.pdf"},
        }
        build_file_preview_mock.return_value = {
            "content": pdf_buffer.getvalue(),
            "filename": "doc.pdf",
            "media_type": "application/pdf",
        }
        png = template_preview_image_service._generate_template_preview_png(
            get_template_mock.return_value,
            "owner",
        )
        self.assertEqual(png, PNG_SIGNATURE)


class PdfPreviewImageTests(unittest.TestCase):
    def test_render_pdf_first_page_to_png_returns_png_bytes(self) -> None:
        from src.generator.generation.pdf_preview_image import render_pdf_first_page_to_png

        pdf_buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(pdf_buffer)
        png = render_pdf_first_page_to_png(pdf_buffer.getvalue())
        if png is None:
            self.skipTest("PyMuPDF is not available in this environment")
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
