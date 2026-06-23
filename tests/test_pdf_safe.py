from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfReader, PdfWriter

from src.generator.generation import pdf_safe


BACKGROUND_ANCHOR = (
    '<w:r><w:drawing><wp:anchor behindDoc="1"><wp:extent cx="914400" cy="914400"/>'
    '<a:blip r:embed="rId14"><a:extLst><a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">'
    '<asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" '
    'r:embed="rId15"/></a:ext></a:extLst></a:blip>'
    '</wp:anchor></w:drawing></w:r>'
)
SIMPLE_SVG = b'<svg viewBox="0 0 10 10" xmlns="http://www.w3.org/2000/svg"><path d="M0 0L10 0L10 10L0 10Z" fill="#EAEAEA"/></svg>'


class PdfSafeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp") / "pdf_safe_tests"
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_prepare_docx_for_pdf_export_strips_kp_background_runs_only_in_staged_copy(self) -> None:
        source = self.tmp_dir / "КП_test.docx"
        staged = self.tmp_dir / "staged.docx"
        document_xml = f"<w:document><w:body><w:p>{BACKGROUND_ANCHOR}<w:r><w:t>Text</w:t></w:r></w:p></w:body></w:document>"
        with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document_xml)

        plan = pdf_safe.prepare_docx_for_pdf_export(source, staged, file_kind="kp", template_docx=source)

        with ZipFile(source) as archive:
            original_xml = archive.read("word/document.xml").decode("utf-8")
        with ZipFile(staged) as archive:
            staged_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertTrue(plan.should_overlay_kp_background)
        self.assertIn('behindDoc="1"', original_xml)
        self.assertNotIn('behindDoc="1"', staged_xml)
        self.assertIn("Text", staged_xml)

    def test_apply_pdf_safe_postprocess_adds_background_content_to_pdf(self) -> None:
        template = self.tmp_dir / "template.docx"
        pdf_path = self.tmp_dir / "input.pdf"
        document_xml = f"<w:document><w:body><w:p>{BACKGROUND_ANCHOR}</w:p></w:body></w:document>"
        rels_xml = (
            '<Relationships>'
            '<Relationship Id="rId15" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/background.svg"/>'
            '</Relationships>'
        )
        with ZipFile(template, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document_xml)
            archive.writestr("word/_rels/document.xml.rels", rels_xml)
            archive.writestr("word/media/background.svg", SIMPLE_SVG)
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with pdf_path.open("wb") as handle:
            writer.write(handle)

        plan = pdf_safe.PdfSafePlan(
            source_docx=template,
            staged_docx=template,
            template_docx=template,
            should_overlay_kp_background=True,
        )
        pdf_safe.apply_pdf_safe_postprocess(pdf_path, plan)

        reader = PdfReader(str(pdf_path))
        self.assertIn(b" rg", reader.pages[0].get_contents().get_data())


if __name__ == "__main__":
    unittest.main()