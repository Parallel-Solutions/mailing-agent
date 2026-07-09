from __future__ import annotations

import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.shared import RGBColor

if "alembic" not in sys.modules:
    alembic_stub = types.ModuleType("alembic")
    alembic_config_stub = types.ModuleType("alembic.config")
    alembic_command_stub = types.ModuleType("alembic.command")
    alembic_config_stub.Config = object
    alembic_stub.command = alembic_command_stub
    sys.modules["alembic"] = alembic_stub
    sys.modules["alembic.config"] = alembic_config_stub
    sys.modules["alembic.command"] = alembic_command_stub
from src.generator.generation import html_kp, pdf_converter

try:
    from src.generator.generation import document_builder
except ModuleNotFoundError as exc:
    document_builder = None
    DOCUMENT_BUILDER_IMPORT_ERROR = exc
else:
    DOCUMENT_BUILDER_IMPORT_ERROR = None
from src.generator.generation.transforms import build_document_context


class HtmlKPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp_test_html_kp")
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _row(self) -> dict:
        return {
            "ID": "1",
            "SUB_RF": "Test region",
            "MUN_R_NAME": "Test district",
            "MUN_NAME": "Test settlement",
            "ADM_NAME": "Test administration",
            "HEAD_FIO": "Ivanov Ivan Ivanovich",
            "EMAIL_OSN": "test@example.com",
            "TEL_OSN": "+70000000000",
        }

    def test_build_kp_html_keeps_images_behind_text(self) -> None:
        context = build_document_context(self._row(), 101)

        html = html_kp.build_kp_html(context)

        self.assertIn(".kp-background", html)
        self.assertIn("z-index: 0", html)
        self.assertIn("pointer-events: none", html)
        self.assertIn(".kp-content", html)
        self.assertIn("z-index: 1", html)
        self.assertLess(html.index("kp-background"), html.index("kp-content"))

    def test_build_kp_html_uses_template_style_profile(self) -> None:
        context = build_document_context(self._row(), 101)
        template_path = self.tmp_dir / "template.docx"
        doc = Document()
        run = doc.add_paragraph().add_run("ADM_NAME")
        run.font.name = "Courier New"
        run.font.color.rgb = RGBColor(12, 34, 56)
        doc.save(template_path)

        html = html_kp.build_kp_html(context, template_path=template_path)

        self.assertIn('--kp-font-family: "Courier New"', html)
        self.assertIn("--kp-primary-color: #0C2238", html)

    def test_html_conversion_uses_separate_gotenberg_html_urls(self) -> None:
        output_path = self.tmp_dir / "html.pdf"
        calls = []

        class FakeResponse:
            content = b"%PDF ok"
            headers = {"content-type": "application/pdf"}

            def raise_for_status(self) -> None:
                return None

        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def post(self, endpoint, files, data=None):
                calls.append(
                    {
                        "endpoint": endpoint,
                        "filename": files["files"][0],
                        "data": data or {},
                    }
                )
                return FakeResponse()

        with (
            patch.object(pdf_converter, "GOTENBERG_BASE_URLS", ("http://libreoffice",)),
            patch.object(pdf_converter, "GOTENBERG_HTML_BASE_URLS", ("http://html",)),
            patch.object(pdf_converter, "_healthy_gotenberg_base_urls", return_value=("http://html",)) as healthy,
            patch.object(pdf_converter.httpx, "Client", FakeClient),
        ):
            result = pdf_converter.convert_html_to_pdf("<html></html>", output_path)

        self.assertEqual(result, output_path)
        self.assertEqual(output_path.read_bytes(), b"%PDF ok")
        healthy.assert_called_once_with(("http://html",))
        self.assertEqual(calls[0]["endpoint"], "http://html/forms/chromium/convert/html")
        self.assertEqual(calls[0]["filename"], "index.html")
        self.assertEqual(calls[0]["data"].get("printBackground"), "true")

    def test_render_html_kp_pdf_retries_until_one_page(self) -> None:
        context = build_document_context(self._row(), 101)
        output_path = self.tmp_dir / "kp.pdf"
        attempts = []

        def fake_convert(html: str, candidate: Path, **kwargs):
            attempts.append(html)
            candidate.write_bytes(b"%PDF fake")
            return candidate

        def fake_validate(path: Path):
            if len(attempts) == 1:
                return {"ok": False, "reason": "page_count", "message": "two pages", "page_count": 2}
            return {"ok": True, "reason": "", "message": "", "page_count": 1}

        with (
            patch.object(html_kp, "convert_html_to_pdf", side_effect=fake_convert),
            patch.object(html_kp, "validate_kp_pdf", side_effect=fake_validate),
        ):
            result = html_kp.render_html_kp_pdf(context, output_path)

        self.assertEqual(result, output_path)
        self.assertTrue(output_path.exists())
        self.assertEqual(len(attempts), 2)
        self.assertFalse(any(output_path.parent.glob("kp.html_try_*.pdf")))

    @unittest.skipIf(document_builder is None, f"document_builder dependencies unavailable: {DOCUMENT_BUILDER_IMPORT_ERROR}")
    def test_generate_documents_for_row_uses_html_direct_pdf_engine(self) -> None:
        row = self._row()
        context = build_document_context(row, 101)
        output_dir = self.tmp_dir / "output"
        batch_dir = self.tmp_dir / "batch"
        templates_dir = self.tmp_dir / "templates"
        templates_dir.mkdir()

        def fake_render(ctx, output_path, **kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF fake")
            return output_path

        with (
            patch.object(document_builder, "KP_GENERATION_ENGINE", "html"),
            patch.object(document_builder, "render_html_kp_pdf", side_effect=fake_render),
        ):
            generated = document_builder.generate_documents_for_row(
                row,
                context,
                output_dir=output_dir,
                batch_docx_dir=batch_dir,
                templates_dir=templates_dir,
                document_mode="kp",
            )

        self.assertIn("kp_pdf", generated)
        self.assertIn("kp_final_pdf", generated)
        self.assertNotIn("kp", generated)
        self.assertNotIn("kp_final_docx", generated)
        self.assertTrue(generated["kp_pdf"].exists())


if __name__ == "__main__":
    unittest.main()
