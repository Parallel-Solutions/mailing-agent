from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from src.generator.generation import generator_agent, pdf_converter


class PdfPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp_test_pdf_pipeline")
        self.tmp_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def test_convert_docx_batch_reuses_existing_valid_pdf(self) -> None:
        docx_path = self.tmp_dir / "document.docx"
        output_dir = self.tmp_dir / "pdf"
        output_dir.mkdir()
        docx_path.write_bytes(b"fake-docx")
        existing_pdf = output_dir / "document.pdf"
        existing_pdf.write_bytes(b"%PDF fake")
        progress_calls = 0

        def progress() -> None:
            nonlocal progress_calls
            progress_calls += 1

        with patch.object(pdf_converter, "_run_backend") as run_backend:
            result = pdf_converter.convert_docx_batch([docx_path], output_dir, progress_callback=progress)

        run_backend.assert_not_called()
        self.assertEqual(result[docx_path], existing_pdf)
        self.assertEqual(progress_calls, 1)

    def test_finalize_generated_files_recovers_missing_pdf_from_final_docx(self) -> None:
        final_dir = self.tmp_dir / "output" / "1_Test"
        batch_pdf_dir = self.tmp_dir / "batch_pdf"
        final_dir.mkdir(parents=True)
        final_docx = final_dir / "kp.docx"
        final_pdf = final_dir / "kp.pdf"
        final_docx.write_bytes(b"fake-docx")
        results = [
            {
                "status": "ok",
                "result_index": 0,
                "generated_files": {
                    "kp": self.tmp_dir / "missing_staged.docx",
                    "kp_final_docx": final_docx,
                    "kp_final_pdf": final_pdf,
                },
            }
        ]

        def fake_convert(docx_paths, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = output_dir / f"{docx_paths[0].stem}.pdf"
            pdf_path.write_bytes(b"%PDF recovered")
            return {docx_paths[0]: pdf_path}

        with patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert):
            generator_agent.finalize_generated_files(results, batch_pdf_dir=batch_pdf_dir, create_pdf=True)

        self.assertTrue(final_pdf.exists())
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["files"]["kp_final_pdf"], str(final_pdf))

    def test_finalize_generated_files_marks_missing_pdf_as_error(self) -> None:
        staged_docx = self.tmp_dir / "batch_docx" / "kp.docx"
        final_dir = self.tmp_dir / "output" / "1_Test"
        final_docx = final_dir / "kp.docx"
        final_pdf = final_dir / "kp.pdf"
        staged_docx.parent.mkdir(parents=True)
        staged_docx.write_bytes(b"fake-docx")
        results = [
            {
                "status": "ok",
                "result_index": 0,
                "generated_files": {
                    "kp": staged_docx,
                    "kp_final_docx": final_docx,
                    "kp_final_pdf": final_pdf,
                },
            }
        ]

        with patch.object(generator_agent, "convert_docx_batch", return_value={staged_docx: None}):
            generator_agent.finalize_generated_files(results, batch_pdf_dir=self.tmp_dir / "batch_pdf", create_pdf=True)

        self.assertTrue(final_docx.exists())
        self.assertFalse(final_pdf.exists())
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("Не удалось создать PDF", results[0]["error"])

    def test_finalize_generated_files_uses_configured_pdf_chunk_size(self) -> None:
        staged_docx = self.tmp_dir / "batch_docx" / "kp.docx"
        final_dir = self.tmp_dir / "output" / "1_Test"
        final_docx = final_dir / "kp.docx"
        final_pdf = final_dir / "kp.pdf"
        staged_docx.parent.mkdir(parents=True)
        staged_docx.write_bytes(b"fake-docx")
        results = [
            {
                "status": "ok",
                "result_index": 0,
                "generated_files": {
                    "kp": staged_docx,
                    "kp_final_docx": final_docx,
                    "kp_final_pdf": final_pdf,
                },
            }
        ]

        def fake_convert(docx_paths, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = output_dir / f"{docx_paths[0].stem}.pdf"
            pdf_path.write_bytes(b"%PDF recovered")
            return {docx_paths[0]: pdf_path}

        with (
            patch.object(generator_agent, "PDF_CHUNK_SIZE", 7),
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert) as convert_mock,
        ):
            generator_agent.finalize_generated_files(results, batch_pdf_dir=self.tmp_dir / "batch_pdf", create_pdf=True)

        self.assertEqual(convert_mock.call_args.kwargs["chunk_size"], 7)


if __name__ == "__main__":
    unittest.main()
