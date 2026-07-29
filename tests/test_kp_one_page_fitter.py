from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.generator.generation.kp_one_page_fitter import (
    BASELINE_FONT_HALF_POINTS,
    KpLayoutError,
    MIN_ALLOWED_FONT_HALF_POINTS,
    fit_docx_to_one_page_pdf,
)


class KpOnePageFitterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp") / "kp_one_page_fitter_tests"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.source = self.tmp_dir / "kp.docx"
        self.output = self.tmp_dir / "out.pdf"
        self.source.write_bytes(b"docx")

    def _validation(self, *, ok: bool, page_count: int = 1) -> dict:
        if ok:
            return {"ok": True, "page_count": page_count}
        return {"ok": False, "reason": "page_count", "page_count": page_count}

    @patch("src.generator.generation.kp_one_page_fitter.validate_kp_pdf")
    @patch("src.generator.generation.template_preview.convert_docx_to_delivery_pdf")
    def test_baseline_one_page(self, convert_mock, validate_mock) -> None:
        validate_mock.return_value = self._validation(ok=True)

        result = fit_docx_to_one_page_pdf(self.source, self.output, company="Test MO")

        self.assertEqual(result.font_half_points, BASELINE_FONT_HALF_POINTS)
        convert_mock.assert_called_once()
        kwargs = convert_mock.call_args.kwargs
        self.assertEqual(kwargs["max_body_font_half_points"], BASELINE_FONT_HALF_POINTS)

    @patch("src.generator.generation.kp_one_page_fitter.validate_kp_pdf")
    @patch("src.generator.generation.template_preview.convert_docx_to_delivery_pdf")
    def test_second_attempt_uses_smaller_font(self, convert_mock, validate_mock) -> None:
        validate_mock.side_effect = [
            self._validation(ok=False, page_count=2),
            self._validation(ok=True, page_count=1),
        ]

        result = fit_docx_to_one_page_pdf(self.source, self.output)

        self.assertEqual(result.font_half_points, 19)
        self.assertEqual(convert_mock.call_count, 2)
        second_kwargs = convert_mock.call_args_list[1].kwargs
        self.assertEqual(second_kwargs["max_body_font_half_points"], 19)

    @patch("src.generator.generation.kp_one_page_fitter.validate_kp_pdf")
    @patch("src.generator.generation.template_preview.convert_docx_to_delivery_pdf")
    def test_fits_at_minimum_allowed_font(self, convert_mock, validate_mock) -> None:
        responses = [self._validation(ok=False, page_count=2)] * 3
        responses.append(self._validation(ok=True, page_count=1))
        validate_mock.side_effect = responses

        result = fit_docx_to_one_page_pdf(self.source, self.output)

        self.assertEqual(result.font_half_points, MIN_ALLOWED_FONT_HALF_POINTS)
        self.assertEqual(convert_mock.call_count, 4)

    @patch("src.generator.generation.kp_one_page_fitter.validate_kp_pdf")
    @patch("src.generator.generation.template_preview.convert_docx_to_delivery_pdf")
    def test_rejects_when_only_smaller_font_would_fit(self, convert_mock, validate_mock) -> None:
        validate_mock.side_effect = [self._validation(ok=False, page_count=2)] * 4

        with self.assertRaises(KpLayoutError):
            fit_docx_to_one_page_pdf(self.source, self.output, company="Long Name MO")

        self.assertEqual(convert_mock.call_count, 4)
        last_kwargs = convert_mock.call_args.kwargs
        self.assertEqual(last_kwargs["max_body_font_half_points"], MIN_ALLOWED_FONT_HALF_POINTS)

    @patch("src.generator.generation.kp_one_page_fitter.validate_kp_pdf")
    @patch("src.generator.generation.template_preview.convert_docx_to_delivery_pdf")
    def test_rejects_when_nothing_fits_even_at_minimum(self, convert_mock, validate_mock) -> None:
        validate_mock.return_value = self._validation(ok=False, page_count=2)

        with self.assertRaises(KpLayoutError) as ctx:
            fit_docx_to_one_page_pdf(self.source, self.output, company="Huge MO")

        self.assertIn("Huge MO", str(ctx.exception))
        self.assertEqual(convert_mock.call_count, 4)


if __name__ == "__main__":
    unittest.main()
