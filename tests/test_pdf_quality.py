from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from pypdf import PdfWriter

from src.generator.generation.pdf_quality import count_pdf_pages, validate_kp_pdf


class PdfQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp_test_pdf_quality")
        self.tmp_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def _write_pdf(self, path: Path, pages: int) -> None:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=595, height=842)
        with path.open("wb") as handle:
            writer.write(handle)

    def test_validate_kp_pdf_accepts_one_page(self) -> None:
        pdf_path = self.tmp_dir / "one-page.pdf"
        self._write_pdf(pdf_path, 1)

        self.assertEqual(count_pdf_pages(pdf_path), 1)
        self.assertTrue(validate_kp_pdf(pdf_path)["ok"])

    def test_validate_kp_pdf_rejects_two_pages(self) -> None:
        pdf_path = self.tmp_dir / "two-pages.pdf"
        self._write_pdf(pdf_path, 2)

        result = validate_kp_pdf(pdf_path)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "page_count")
        self.assertEqual(result["page_count"], 2)


if __name__ == "__main__":
    unittest.main()
