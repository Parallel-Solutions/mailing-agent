from __future__ import annotations

import unittest
from pathlib import Path

from src.campaigns import template_import_service as tis
from src.generator.generation.import_visual_qa import render_and_score
from src.generator.generation.pdf_fixed_layout import extract_fixed_layout
from src.generator.generation.pdf_preview_image import render_pdf_first_page_to_png

_FULL_PAGE_SCORE_MIN = 0.93


class PismoSlImportSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = Path(__file__).resolve().parent / "fixtures" / "pismo_sl.pdf"
        if not cls.fixture.is_file():
            raise unittest.SkipTest("tests/fixtures/pismo_sl.pdf missing")
        cls.pdf_bytes = cls.fixture.read_bytes()

    def _fixed_prepared(self) -> tuple[str, int]:
        draft_html, _plain_text, _preview_pngs, width = extract_fixed_layout(
            self.pdf_bytes,
            content_width=640,
        )
        prepared = tis._prepare_import_html(draft_html, content_width=width)
        return prepared, width

    def test_fixed_layout_has_marker_and_background(self) -> None:
        prepared, _ = self._fixed_prepared()
        self.assertIn('data-layout="fixed"', prepared)
        self.assertIn('src="data:image/png;base64,', prepared)
        self.assertIn("position:absolute", prepared)
        self.assertIn("fixed-text-view", prepared)

    def test_import_pdf_uses_fixed_layout(self) -> None:
        _name, _subject, body, source, _qa = tis._convert_to_html("pismo_sl.pdf", self.pdf_bytes)
        self.assertEqual(source, "fixed_layout")
        self.assertIn('data-layout="fixed"', body)
        self.assertIn("ПОЛУЧИТЬ ПОДБОРКУ", body)

    def test_fixed_layout_render_score_meets_threshold(self) -> None:
        prepared, width = self._fixed_prepared()
        page_png = render_pdf_first_page_to_png(self.pdf_bytes, scale=2.0)
        if not page_png:
            self.skipTest("pdf page png unavailable")
        score, _ = render_and_score(prepared, [page_png], viewport_width=width)
        self.assertGreaterEqual(score, _FULL_PAGE_SCORE_MIN)

    def test_fixed_layout_preserves_variables_and_links(self) -> None:
        prepared, _ = self._fixed_prepared()
        self.assertIn("{{Имя}}", prepared)
        self.assertIn("{{Отчество}}", prepared)
        self.assertIn("{{Компания}}", prepared)
        self.assertIn("ciales.ru", prepared)

    def test_fixed_layout_emits_letter_spacing(self) -> None:
        prepared, _ = self._fixed_prepared()
        self.assertIn("letter-spacing:", prepared)


if __name__ == "__main__":
    unittest.main()
