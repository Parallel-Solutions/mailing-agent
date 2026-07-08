from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from openpyxl import Workbook

from src.generator.generation.document_builder import KP_TEMPLATE_FILENAME
from src.generator.generation.template_analysis import analyze_template_file, build_template_analysis_context


def _workspace_temp_dir() -> tempfile.TemporaryDirectory[str]:
    root = Path(tempfile.gettempdir()) / "mailing-agent-tests"
    root.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=str(root))


class TemplateAnalysisTests(unittest.TestCase):
    def test_analyze_docx_extracts_placeholders_and_tables(self) -> None:
        with _workspace_temp_dir() as tmp:
            template_path = Path(tmp) / KP_TEMPLATE_FILENAME
            doc = Document()
            doc.add_paragraph("Уважаемый HEAD_FIO!")
            table = doc.add_table(rows=1, cols=1)
            table.cell(0, 0).text = "КП для ADM_NAME"
            doc.save(template_path)

            result = analyze_template_file(template_path, kind="kp")

            self.assertTrue(result["exists"])
            self.assertEqual(result["format"], "docx")
            self.assertIn("HEAD_FIO", result["placeholders"])
            self.assertIn("ADM_NAME", result["placeholders"])
            self.assertGreaterEqual(result["table_count"], 1)
            self.assertTrue(any(block["kind"] == "table" for block in result["blocks"]))

    def test_build_template_analysis_includes_data_headers(self) -> None:
        with _workspace_temp_dir() as tmp:
            root = Path(tmp)
            templates_dir = root / "templates"
            input_dir = root / "input"
            templates_dir.mkdir()
            input_dir.mkdir()

            template_path = templates_dir / KP_TEMPLATE_FILENAME
            doc = Document()
            doc.add_paragraph("Коммерческое предложение для ADM_NAME и HEAD_FIO")
            doc.save(template_path)

            data_path = input_dir / "data.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.cell(row=2, column=1).value = "ADM_NAME"
            worksheet.cell(row=2, column=2).value = "EMAIL"
            worksheet.cell(row=3, column=1).value = "Администрация тестового района"
            worksheet.cell(row=3, column=2).value = "test@example.com"
            workbook.save(data_path)

            fake_paths = SimpleNamespace(templates_dir=templates_dir, data_xlsx=data_path)
            with patch("src.generator.generation.template_analysis.resolve_job_paths", return_value=fake_paths):
                result = build_template_analysis_context(job_id="job-test", document_mode="kp")

            self.assertEqual(result["data"]["row_count"], 1)
            self.assertIn("ADM_NAME", result["data"]["headers"])
            self.assertIn("HEAD_FIO", result["all_placeholders"])
            self.assertIn("HEAD_FIO", result["placeholders_without_same_named_column"])
            self.assertNotIn("ADM_NAME", result["placeholders_without_same_named_column"])


if __name__ == "__main__":
    unittest.main()
