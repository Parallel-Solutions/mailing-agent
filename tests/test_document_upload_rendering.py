from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.campaigns import template_service


class DocumentUploadRenderingTests(unittest.TestCase):
    @patch("src.generator.generation.template_preview.convert_docx_to_delivery_pdf")
    def test_docx_uses_generic_renderer_independent_of_filename(self, convert_mock) -> None:
        observed: dict[str, object] = {}

        def convert(source: Path, output: Path, **kwargs: object) -> Path:
            observed["source_name"] = source.name
            observed["kwargs"] = kwargs
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"%PDF-1.4 generic")
            return output

        convert_mock.side_effect = convert

        data, filename = template_service._build_document_pdf_artifact(
            "КП_Территориальные_зоны.docx",
            b"docx payload",
        )

        self.assertEqual(data, b"%PDF-1.4 generic")
        self.assertEqual(filename, "КП_Территориальные_зоны.pdf")
        self.assertEqual(observed["source_name"], "source.docx")
        self.assertEqual(
            observed["kwargs"],
            {"file_kind": None, "template_docx": None},
        )
        convert_mock.assert_called_once()

    @patch("src.generator.generation.pdf_converter.convert_html_to_pdf")
    def test_html_also_produces_delivery_pdf(self, convert_mock) -> None:
        def convert(_html: str, output: Path, **_kwargs: object) -> Path:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"%PDF-1.4 html")
            return output

        convert_mock.side_effect = convert

        data, filename = template_service._build_document_pdf_artifact(
            "document.html",
            "<p>Документ</p>".encode(),
        )

        self.assertEqual(data, b"%PDF-1.4 html")
        self.assertEqual(filename, "document.pdf")
        convert_mock.assert_called_once()

    @patch("src.campaigns.template_service.logger.exception")
    @patch(
        "src.generator.generation.template_preview.convert_docx_to_delivery_pdf",
        side_effect=RuntimeError("converter unavailable"),
    )
    def test_converter_failure_is_wrapped_for_api(self, _convert_mock, log_mock) -> None:
        with self.assertRaises(template_service.DocumentConversionError) as context:
            template_service._build_document_pdf_artifact("document.docx", b"docx payload")

        self.assertEqual(context.exception.code, "document_conversion_failed")
        self.assertEqual(str(context.exception), "Документ не удалось преобразовать в PDF.")
        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        log_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
