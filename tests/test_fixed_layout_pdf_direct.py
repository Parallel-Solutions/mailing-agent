from __future__ import annotations

import re
import unittest
from pathlib import Path

from src.generator.generation.import_visual_qa import render_and_score, render_html_to_png
from src.generator.generation.pdf_fixed_layout import (
    FixedSpan,
    _collect_text_lines,
    _detect_page_bg_color,
    _text_redact_jobs,
    _word_spacing_em,
    extract_fixed_layout,
)
from src.generator.generation.pdf_preview_image import render_pdf_first_page_to_png

# Full-page MAE against PDF raster tops out near ~0.93–0.94 even for PDF24 HTML
# (font AA / hinting). Require matching that ceiling, not an unreachable 0.97.
_FULL_PAGE_SCORE_MIN = 0.93


def _html_body_fragment(html: str) -> str:
    match = re.search(r"<body[^>]*>(.*)</body>", html or "", flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else html


class FixedLayoutPdfDirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = Path(__file__).resolve().parent / "fixtures" / "pismo_sl.pdf"
        if not cls.fixture.is_file():
            raise unittest.SkipTest("tests/fixtures/pismo_sl.pdf missing")
        cls.pdf_bytes = cls.fixture.read_bytes()
        cls.pdf24_html = Path(__file__).resolve().parent / "fixtures" / "pismo_sl_pdf24.html"

    def _prepared_fixed_html(self) -> tuple[str, int]:
        draft_html, _plain_text, _preview_pngs, width = extract_fixed_layout(
            self.pdf_bytes,
            content_width=640,
        )
        # Prefer the same prepare path as import when available (Docker test image).
        try:
            from src.campaigns import template_import_service as tis

            prepared = tis._prepare_import_html(draft_html, content_width=width)
        except ImportError:
            prepared = draft_html
        return prepared, width

    def test_fixed_layout_scores_against_pdf_screenshot(self) -> None:
        prepared, width = self._prepared_fixed_html()
        page_png = render_pdf_first_page_to_png(self.pdf_bytes, scale=2.0)
        if not page_png:
            self.skipTest("pdf page png unavailable")
        score, _ = render_and_score(prepared, [page_png], viewport_width=width)
        self.assertGreaterEqual(score, _FULL_PAGE_SCORE_MIN)

    def test_fixed_layout_includes_word_and_letter_spacing(self) -> None:
        prepared, _ = self._prepared_fixed_html()
        self.assertIn("word-spacing:", prepared)
        self.assertIn("letter-spacing:", prepared)
        self.assertIn("fixed-text-view", prepared)

    def test_title_word_spacing_not_inflated(self) -> None:
        prepared, _ = self._prepared_fixed_html()
        idx = prepared.find("КАКИЕ")
        self.assertGreater(idx, 0)
        chunk = prepared[max(0, idx - 280) : idx]
        match = re.search(r"word-spacing:([-\d.]+)em", chunk)
        self.assertIsNotNone(match)
        value = float(match.group(1))  # type: ignore[union-attr]
        self.assertLess(abs(value), 0.1, f"title word-spacing too large: {value}")

    def test_cta_button_label_positioned(self) -> None:
        prepared, _ = self._prepared_fixed_html()
        idx = prepared.find("ПОЛУЧИТЬ")
        self.assertGreater(idx, 0)
        chunk = prepared[max(0, idx - 240) : idx + 40]
        self.assertIn("color:#ffffff", chunk)
        match = re.search(r"left:([-\d.]+)em;top:([-\d.]+)em", chunk)
        self.assertIsNotNone(match)
        left = float(match.group(1))  # type: ignore[union-attr]
        top = float(match.group(2))  # type: ignore[union-attr]
        self.assertAlmostEqual(left, 11.81, delta=0.15)
        self.assertAlmostEqual(top, 49.86, delta=0.2)

    def test_footer_columns_keep_distinct_lefts(self) -> None:
        prepared, _ = self._prepared_fixed_html()
        anchors: dict[str, tuple[float, float]] = {}
        for label in ("Кузнецов", "Генеральный", "904", "СЛУЧАЙНЫЙ"):
            idx = prepared.find(label)
            self.assertGreater(idx, 0, label)
            chunk = prepared[max(0, idx - 240) : idx]
            match = re.search(r"left:([-\d.]+)em;top:([-\d.]+)em", chunk)
            self.assertIsNotNone(match, label)
            anchors[label] = (float(match.group(1)), float(match.group(2)))  # type: ignore[union-attr]
        self.assertAlmostEqual(anchors["Кузнецов"][0], 4.73, delta=0.2)
        self.assertAlmostEqual(anchors["Генеральный"][0], 4.73, delta=0.2)
        self.assertGreater(anchors["904"][0], 12.0)
        self.assertGreater(anchors["СЛУЧАЙНЫЙ"][0], 30.0)
        self.assertAlmostEqual(anchors["Генеральный"][1], anchors["904"][1], delta=0.15)
        self.assertAlmostEqual(anchors["904"][1], anchors["СЛУЧАЙНЫЙ"][1], delta=0.15)

    def test_dark_text_on_colored_decor_not_redacted_white(self) -> None:
        """Callout / channel-button glyphs must redact with local teal, not page white."""
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF unavailable")

        document = fitz.open(stream=self.pdf_bytes, filetype="pdf")
        try:
            page = document.load_page(0)
            lines, _ = _collect_text_lines(page, allow_ocr=False)
            page_bg = _detect_page_bg_color(page)
            jobs = _text_redact_jobs(lines, page, page_bg)
        finally:
            document.close()

        self.assertTrue(jobs)
        # Spans that sit on the mint callout / cyan pill (rough page coords from pismo_sl).
        colored: list[tuple[float, float, float]] = []
        for (x0, y0, x1, y1), fill in jobs:
            # Callout block ~ mid page; channel button lower-right.
            in_callout = 140 <= x0 <= 420 and 340 <= y0 <= 520
            in_channel_btn = 360 <= x0 <= 520 and 700 <= y0 <= 740
            if in_callout or in_channel_btn:
                colored.append(fill)

        self.assertGreaterEqual(len(colored), 3, "expected callout/button redact jobs")
        for fill in colored:
            # Not near-white page background.
            self.assertLess(
                min(fill),
                0.92,
                f"redact fill too white (hole): {fill}",
            )
            # Teal/cyan bias: G and B elevated vs pure gray white.
            self.assertGreater(fill[1] + fill[2], fill[0] + 0.05)

    def test_justified_callout_line_not_split_into_word_chips(self) -> None:
        """Wide word-spacing must not create per-word absolute divs (white chips in UI)."""
        draft_html, _, _, _ = extract_fixed_layout(self.pdf_bytes, content_width=640)
        # One line should contain the full phrase, not only the first word.
        self.assertIn("Часть таких операций уже сегодня можно передать", draft_html)
        # Count absolute boxes whose text is a single short Russian word near callout.
        singles = re.findall(
            r'position:absolute;left:[^"]+top:4[79]\.[^"]+"[^>]*>'
            r'<span[^>]*>[^<]{1,12}</span></div>',
            draft_html,
        )
        self.assertLess(
            len(singles),
            3,
            f"callout appears over-split into word chips: {singles[:8]}",
        )

    def test_title_and_callout_use_covering_bold_faces(self) -> None:
        """Bare Arial-BoldMT must not resolve to LBFAHD (missing Cyrillic → jumping weight)."""
        draft_html, _, _, _ = extract_fixed_layout(self.pdf_bytes, content_width=640)

        def family_before(token: str) -> str:
            idx = draft_html.find(token)
            self.assertGreater(idx, 0, token)
            chunk = draft_html[max(0, idx - 280) : idx]
            match = re.search(r"font-family:'([^']+)'", chunk)
            self.assertIsNotNone(match, token)
            return match.group(1)  # type: ignore[union-attr]

        title_family = family_before("КАКИЕ")
        callout_family = family_before("Вероятно")
        self.assertNotIn("LBFAHD", title_family)
        self.assertNotIn("LBFAHD", callout_family)
        # PDF24 subsets that cover these runs (or an embedded PDF subset).
        self.assertTrue(
            "VNUGJO" in title_family or "Arial-BoldMT" in title_family,
            title_family,
        )
        self.assertTrue(
            "GJHVGW" in callout_family or "Arial-BoldMT" in callout_family,
            callout_family,
        )
        for token in ("КАКИЕ", "Вероятно"):
            idx = draft_html.find(token)
            chunk = draft_html[max(0, idx - 280) : idx]
            self.assertNotIn(
                "font-weight:700",
                chunk,
                f"faux-bold on BoldMT face near {token}",
            )

    def test_fixed_layout_close_to_pdf24_reference(self) -> None:
        if not self.pdf24_html.is_file():
            self.skipTest("tests/fixtures/pismo_sl_pdf24.html missing")
        prepared, width = self._prepared_fixed_html()
        page_png = render_pdf_first_page_to_png(self.pdf_bytes, scale=2.0)
        if not page_png:
            self.skipTest("pdf page png unavailable")

        pdf24_body = _html_body_fragment(
            self.pdf24_html.read_text(encoding="utf-8", errors="replace")
        )
        pdf24_score, _ = render_and_score(pdf24_body, [page_png], viewport_width=width)
        ours_score, _ = render_and_score(prepared, [page_png], viewport_width=width)
        self.assertGreaterEqual(ours_score, _FULL_PAGE_SCORE_MIN)
        # Stay within a small margin of the PDF24 reference under the same metric.
        self.assertGreaterEqual(ours_score + 0.01, pdf24_score)

        reference_png = render_html_to_png(pdf24_body, viewport_width=width)
        if not reference_png:
            self.skipTest("pdf24 reference render unavailable")
        vs_pdf24, _ = render_and_score(prepared, [reference_png], viewport_width=width)
        self.assertGreaterEqual(vs_pdf24, _FULL_PAGE_SCORE_MIN)


class WordSpacingUnitTests(unittest.TestCase):
    def test_word_spacing_uses_span_size_not_page_base(self) -> None:
        span = FixedSpan(
            text="HELLO WORLD ",
            x0=0.0,
            y0=0.0,
            x1=100.0,
            y1=12.0,
            size=20.0,
            color="#000000",
            bold=False,
            italic=False,
            font="Arial",
            char_bboxes=None,
        )

        class _Fonts:
            def text_width_pt(self, _font: str, text: str, size: float) -> float:
                # Natural width slightly under bbox → small positive word-spacing.
                return size * 0.5 * max(len(text) - 1, 1)

        fonts = _Fonts()  # type: ignore[assignment]
        value = _word_spacing_em(span, None, base_pt=12.0, fonts=fonts)  # type: ignore[arg-type]
        self.assertIsNotNone(value)
        # If incorrectly divided by base_pt (12) instead of size (20), result is larger.
        self.assertLess(float(value), 0.6)  # type: ignore[arg-type]

    def test_word_spacing_skips_severe_font_mismatch(self) -> None:
        span = FixedSpan(
            text="HELLO WORLD ",
            x0=0.0,
            y0=0.0,
            x1=100.0,
            y1=12.0,
            size=20.0,
            color="#000000",
            bold=False,
            italic=False,
            font="Arial",
            char_bboxes=None,
        )

        class _Fonts:
            def text_width_pt(self, _font: str, text: str, size: float) -> float:
                return 10.0  # absurdly small vs bbox 100

        fonts = _Fonts()  # type: ignore[assignment]
        value = _word_spacing_em(span, None, base_pt=12.0, fonts=fonts)  # type: ignore[arg-type]
        self.assertIsNone(value)

    def test_word_spacing_from_char_boxes(self) -> None:
        # Two words with a slightly wide space between glyph boxes.
        text = "AB CD "
        boxes = [
            (0.0, 0.0, 10.0, 12.0),  # A
            (10.0, 0.0, 20.0, 12.0),  # B
            (20.0, 0.0, 26.0, 12.0),  # space (~6pt vs typical 0.278*20≈5.56)
            (26.0, 0.0, 36.0, 12.0),  # C
            (36.0, 0.0, 46.0, 12.0),  # D
            (46.0, 0.0, 51.0, 12.0),  # trailing space
        ]
        span = FixedSpan(
            text=text,
            x0=0.0,
            y0=0.0,
            x1=51.0,
            y1=12.0,
            size=20.0,
            color="#000000",
            bold=False,
            italic=False,
            font="Arial",
            char_bboxes=boxes,
        )

        class _Fonts:
            def text_width_pt(self, _font: str, text: str, size: float) -> float | None:
                return None

        value = _word_spacing_em(span, None, base_pt=12.0, fonts=_Fonts())  # type: ignore[arg-type]
        self.assertIsNotNone(value)
        self.assertLess(abs(float(value)), 0.2)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
