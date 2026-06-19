from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from src.generator.generation import document_builder
from src.generator.generation.structured_kp import build_structured_kp_model, render_structured_kp_docx
from src.generator.generation.transforms import build_document_context


class StructuredKPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp") / "structured_kp_tests"
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _row(self) -> dict:
        return {
            "ID": "1",
            "SUB_RF": "Республика Адыгея",
            "MUN_R_NAME": "Тахтамукайский муниципальный район",
            "MUN_NAME": "Яблоновское городское поселение",
            "ADM_NAME": "Администрация Яблоновского городского поселения",
            "HEAD_FIO": "Иванов Иван Иванович",
            "EMAIL_OSN": "test@example.com",
            "TEL_OSN": "+70000000000",
        }

    def test_build_structured_kp_model_uses_generation_context(self) -> None:
        context = build_document_context(self._row(), 101)

        model = build_structured_kp_model(context)

        self.assertEqual(model.outgoing_number, "101")
        self.assertIn("Яблоновского городского поселения", model.work_scope)
        self.assertIn("местных нормативов", model.work_title)
        self.assertEqual(model.price.amount_rubles, 99000)
        self.assertEqual(model.price.amount_words, "девяносто девять тысяч")

    def test_render_structured_kp_docx_creates_document_without_placeholders(self) -> None:
        context = build_document_context(self._row(), 101)
        output_path = self.tmp_dir / "structured_kp.docx"

        render_structured_kp_docx(context, output_path)

        doc = Document(output_path)
        text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
        table_text = "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
        full_text = f"{text}\n{table_text}"
        self.assertIn("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ", full_text)
        self.assertIn("Яблоновского городского поселения", full_text)
        self.assertIn("99 000,00", full_text)
        self.assertNotIn("MUN_", full_text)
        self.assertNotIn("ADM_", full_text)

    def test_generate_documents_for_row_can_use_structured_kp_without_template(self) -> None:
        row = self._row()
        context = build_document_context(row, 101)
        output_dir = self.tmp_dir / "output"
        batch_dir = self.tmp_dir / "batch"
        templates_dir = self.tmp_dir / "templates"
        templates_dir.mkdir()

        with patch.object(document_builder, "KP_GENERATION_ENGINE", "structured"):
            generated = document_builder.generate_documents_for_row(
                row,
                context,
                output_dir=output_dir,
                batch_docx_dir=batch_dir,
                templates_dir=templates_dir,
                document_mode="kp",
            )

        self.assertIn("kp", generated)
        self.assertTrue(generated["kp"].exists())
        self.assertTrue(generated["kp_final_docx"].name.startswith("КП_"))
        self.assertIn("Яблоновское городское поселение", generated["kp_final_docx"].name)


if __name__ == "__main__":
    unittest.main()
