from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
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

    def test_explicit_kp_postprocess_restores_two_contact_icons(self) -> None:
        source = self.tmp_dir / "source.docx"
        staged = self.tmp_dir / "staged-icons.docx"
        pdf_path = self.tmp_dir / "icons.pdf"

        def drawing(png_id: str, svg_id: str, cx: int, cy: int) -> str:
            return (
                '<w:r><w:drawing><wp:inline><wp:extent cx="{}" cy="{}"/>'.format(cx, cy)
                + '<a:blip r:embed="{}"><a:extLst><a:ext>'.format(png_id)
                + '<asvg:svgBlip r:embed="{}"/>'.format(svg_id)
                + "</a:ext></a:extLst></a:blip></wp:inline></w:drawing></w:r>"
            )

        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main"><w:body>'
            + drawing("rId12", "rId13", 113030, 121285)
            + drawing("rId14", "rId15", 118745, 80645)
            + "<w:p><w:r><w:t>\u0442\u0435\u043b. 8 903 806-08-08</w:t></w:r></w:p>"
            + "<w:p><w:r><w:t>a.ivanov@parresh.ru</w:t></w:r></w:p>"
            + "</w:body></w:document>"
        )
        rels_xml = (
            "<Relationships>"
            '<Relationship Id="rId13" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/phone.svg"/>'
            '<Relationship Id="rId15" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/email.svg"/>'
            "</Relationships>"
        )
        with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document_xml)
            archive.writestr("word/_rels/document.xml.rels", rels_xml)
            archive.writestr("word/media/phone.svg", SIMPLE_SVG)
            archive.writestr("word/media/email.svg", SIMPLE_SVG)

        plan = pdf_safe.prepare_docx_for_pdf_export(
            source,
            staged,
            file_kind="kp",
            template_docx=source,
        )

        self.assertTrue(plan.should_overlay_kp_contact_icons)
        self.assertEqual(len(pdf_safe.extract_kp_contact_icons(source)), 2)
        with ZipFile(staged) as archive:
            staged_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertNotIn("rId13", staged_xml)
        self.assertNotIn("rId15", staged_xml)

        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with pdf_path.open("wb") as handle:
            writer.write(handle)
        positions = pdf_safe.KpContactTextPositions(phone_x=72.0, phone_y=100.0, email_y=82.0)
        with patch.object(pdf_safe, "find_contact_text_positions", return_value=positions):
            pdf_safe.apply_pdf_safe_postprocess(pdf_path, plan)

        content = PdfReader(str(pdf_path)).pages[0].get_contents().get_data()
        self.assertIn(b"/GSicon gs", content)
        self.assertGreaterEqual(content.count(b" rg"), 2)

    def test_contact_positions_join_split_fragments_and_apply_page_transform(self) -> None:
        class FragmentedContactPage:
            def extract_text(self, *, visitor_text):
                page_transform = [1.0, 0.0, 0.0, -1.0, 0.0, 841.0]

                def emit(text: str, x: float, source_y: float) -> None:
                    visitor_text(
                        text,
                        page_transform,
                        [0.05, 0.0, 0.0, -0.05, x, source_y],
                        None,
                        180.0,
                    )

                # Website text in the header must not be mistaken for the contact email.
                visitor_text("parresh", page_transform, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], None, 161.0)
                emit("\u0418\u0441\u043f", 47.28, 664.85)
                emit(".", 64.80, 664.85)
                emit("\u0427\u0435\u0440\u043a\u0430\u0448\u0438\u043d\u0430", 84.00, 664.85)
                emit("\u0442\u0435\u043b", 66.84, 682.25)
                emit(".", 80.64, 682.25)
                emit("ks", 66.84, 700.609)
                emit("@", 75.24, 700.609)
                emit("parresh", 83.40, 700.609)
                emit(".ru", 113.40, 700.609)

        positions = pdf_safe.find_contact_text_positions(FragmentedContactPage())  # type: ignore[arg-type]

        self.assertAlmostEqual(positions.author_x or 0.0, 47.28, places=2)
        self.assertAlmostEqual(positions.phone_x or 0.0, 66.84, places=2)
        self.assertAlmostEqual(positions.phone_y or 0.0, 158.75, places=2)
        self.assertAlmostEqual(positions.email_y or 0.0, 140.391, places=3)

    def test_convert_docx_to_delivery_pdf_runs_pdf_safe_pipeline(self) -> None:
        from src.generator.generation.template_preview import convert_docx_to_delivery_pdf

        source = self.tmp_dir / "КП_test.docx"
        output = self.tmp_dir / "delivery.pdf"
        with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Text</w:t></w:r></w:p></w:body></w:document>")
        output.write_bytes(b"%PDF-1.4")

        with patch(
            "src.generator.generation.template_preview._convert_preview_docx_to_pdf",
            return_value=output,
        ) as convert_mock, patch(
            "src.generator.generation.pdf_safe.prepare_docx_for_pdf_export",
            return_value=pdf_safe.PdfSafePlan(source_docx=source, staged_docx=source),
        ) as prepare_mock, patch(
            "src.generator.generation.pdf_safe.apply_pdf_safe_postprocess",
        ) as postprocess_mock:
            result = convert_docx_to_delivery_pdf(source, output, file_kind="kp", template_docx=source)

        prepare_mock.assert_called_once()
        convert_mock.assert_called_once()
        postprocess_mock.assert_called_once()
        self.assertEqual(result, output)


if __name__ == "__main__":
    unittest.main()
