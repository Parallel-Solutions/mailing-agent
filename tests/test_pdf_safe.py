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

    def test_prepare_docx_for_pdf_export_shrinks_only_mngp_kp_body(self) -> None:
        source = self.tmp_dir / "KP_test.docx"
        staged = self.tmp_dir / "staged.docx"
        title = "\u041a\u041e\u041c\u041c\u0415\u0420\u0427\u0415\u0421\u041a\u041e\u0415 \u041f\u0420\u0415\u0414\u041b\u041e\u0416\u0415\u041d\u0418\u0415"
        body = (
            "\u041e\u041e\u041e \u00ab\u041f\u0430\u0440\u0430\u043b\u043b\u0435\u043b\u044c\u043d\u044b\u0435 \u0420\u0435\u0448\u0435\u043d\u0438\u044f\u00bb \u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u0435\u0442 \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0442\u044b "
            "\u043f\u043e \u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u043a\u0435 \u043f\u0440\u043e\u0435\u043a\u0442\u0430 \u043c\u0435\u0441\u0442\u043d\u044b\u0445 \u043d\u043e\u0440\u043c\u0430\u0442\u0438\u0432\u043e\u0432 \u0433\u0440\u0430\u0434\u043e\u0441\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0433\u043e \u043f\u0440\u043e\u0435\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f."
        )
        deadline = "\u0421\u0440\u043e\u043a \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f \u043a\u043e\u043c\u043c\u0435\u0440\u0447\u0435\u0441\u043a\u043e\u0433\u043e \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f: \u0434\u043e 31.07.2026."
        signature = "\u0421 \u0443\u0432\u0430\u0436\u0435\u043d\u0438\u0435\u043c,"

        def paragraph(text: str, size: str) -> str:
            return (
                '<w:p><w:r><w:rPr><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
                '<w:t>{text}</w:t></w:r></w:p>'
            ).format(size=size, text=text)

        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + paragraph(title, "32")
            + paragraph(body, "24")
            + paragraph(deadline, "24")
            + paragraph(signature, "28")
            + '</w:body></w:document>'
        )
        with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document_xml)

        pdf_safe.prepare_docx_for_pdf_export(source, staged, file_kind="kp", template_docx=source)

        with ZipFile(staged) as archive:
            staged_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn(f"<w:t>{body}</w:t>", staged_xml)
        self.assertIn(f"<w:t>{signature}</w:t>", staged_xml)
        self.assertIn('<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr><w:t>' + body, staged_xml)
        self.assertIn('<w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr><w:t>' + signature, staged_xml)

    def test_prepare_docx_for_pdf_export_shrinks_generic_kp_body_after_title(self) -> None:
        source = self.tmp_dir / "KP_generic.docx"
        staged = self.tmp_dir / "staged_generic.docx"
        title = "\u041a\u041e\u041c\u041c\u0415\u0420\u0427\u0415\u0421\u041a\u041e\u0415 \u041f\u0420\u0415\u0414\u041b\u041e\u0416\u0415\u041d\u0418\u0415"
        body = "\u041b\u044e\u0431\u043e\u0439 \u0434\u0440\u0443\u0433\u043e\u0439 \u0442\u0435\u043a\u0441\u0442 \u043a\u043e\u043c\u043c\u0435\u0440\u0447\u0435\u0441\u043a\u043e\u0433\u043e \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f."
        deadline = "\u0421\u0440\u043e\u043a \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f \u043a\u043e\u043c\u043c\u0435\u0440\u0447\u0435\u0441\u043a\u043e\u0433\u043e \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f: \u0434\u043e 31.07.2026."
        signature = "\u0421 \u0443\u0432\u0430\u0436\u0435\u043d\u0438\u0435\u043c,"

        def paragraph(text: str, size: str) -> str:
            return (
                '<w:p><w:r><w:rPr><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
                '<w:t>{text}</w:t></w:r></w:p>'
            ).format(size=size, text=text)

        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + paragraph(title, "32")
            + paragraph(body, "24")
            + paragraph(deadline, "24")
            + paragraph(signature, "28")
            + '</w:body></w:document>'
        )
        with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document_xml)

        pdf_safe.prepare_docx_for_pdf_export(
            source,
            staged,
            file_kind="kp",
            template_docx=source,
            max_body_font_half_points=18,
        )

        with ZipFile(staged) as archive:
            staged_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn('<w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr><w:t>' + title, staged_xml)
        self.assertIn('<w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr><w:t>' + body, staged_xml)
        self.assertIn('<w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr><w:t>' + deadline, staged_xml)
        self.assertIn('<w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr><w:t>' + signature, staged_xml)

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
