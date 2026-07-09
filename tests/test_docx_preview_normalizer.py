from __future__ import annotations

import shutil
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from lxml import etree

from src.generator.generation.docx_preview_normalizer import normalize_docx_for_preview


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def paragraph_xml(text: str, size: int = 24) -> str:
    return (
        '<w:p>'
        '<w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>'
        '<w:r>'
        f'<w:rPr><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
        f'<w:t>{text}</w:t>'
        '</w:r>'
        '</w:p>'
    )


def document_xml(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document '
        f'xmlns:w="{W_NS}" '
        f'xmlns:wp="{WP_NS}" '
        f'xmlns:a="{A_NS}">'
        f'<w:body>{body}</w:body>'
        '</w:document>'
    ).encode("utf-8")


def anchor_xml(*, behind_doc: str = "0", cx: int = 1000000, cy: int = 1000000) -> str:
    return (
        f'<wp:anchor behindDoc="{behind_doc}" allowOverlap="1" layoutInCell="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        '<wp:docPr id="1" name="stamp"/>'
        '<wp:cNvGraphicFramePr/>'
        '<a:graphic><a:graphicData/></a:graphic>'
        '</wp:anchor>'
    )


def make_docx(path: Path, xml_payload: bytes) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml", xml_payload)


def read_document_xml(path: Path) -> etree._Element:
    with zipfile.ZipFile(path) as archive:
        payload = archive.read("word/document.xml")
    return etree.fromstring(payload)


@contextmanager
def workspace_temp_dir():
    base = Path.cwd() / "tmp-test-write"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class DocxPreviewNormalizerTests(unittest.TestCase):
    def test_foreground_anchor_stays_positioned_but_goes_behind_text(self) -> None:
        with workspace_temp_dir() as tmp:
            source = Path(tmp) / "source.docx"
            target = Path(tmp) / "target.docx"
            make_docx(source, document_xml(paragraph_xml(anchor_xml())))

            report = normalize_docx_for_preview(source, target)
            root = read_document_xml(target)
            anchors = root.findall(f".//{qn(WP_NS, 'anchor')}")

            self.assertEqual(report.foreground_anchors_normalized, 1)
            self.assertEqual(report.foreground_anchors_inlined, 0)
            self.assertEqual(len(anchors), 1)
            self.assertEqual(len(root.findall(f".//{qn(WP_NS, 'inline')}")), 0)
            self.assertEqual(anchors[0].get("behindDoc"), "1")
            self.assertEqual(anchors[0].get("allowOverlap"), "0")
            self.assertEqual(anchors[0].get("layoutInCell"), "1")

    def test_large_background_anchor_stays_behind_text(self) -> None:
        with workspace_temp_dir() as tmp:
            source = Path(tmp) / "source.docx"
            target = Path(tmp) / "target.docx"
            make_docx(source, document_xml(paragraph_xml(anchor_xml(behind_doc="1", cx=5000000, cy=5000000))))

            report = normalize_docx_for_preview(source, target)
            root = read_document_xml(target)
            anchors = root.findall(f".//{qn(WP_NS, 'anchor')}")

            self.assertEqual(report.background_anchors_kept, 1)
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0].get("behindDoc"), "1")
            self.assertEqual(anchors[0].get("allowOverlap"), "0")
            self.assertEqual(anchors[0].get("layoutInCell"), "1")

    def test_compact_body_does_not_touch_signature_block(self) -> None:
        with workspace_temp_dir() as tmp:
            source = Path(tmp) / "source.docx"
            target = Path(tmp) / "target.docx"
            body = "".join(
                [
                    paragraph_xml("COMMERCIAL TITLE", 28),
                    paragraph_xml("\u041a\u041e\u041c\u041c\u0415\u0420\u0427\u0415\u0421\u041a\u041e\u0415 \u041f\u0420\u0415\u0414\u041b\u041e\u0416\u0415\u041d\u0418\u0415", 28),
                    paragraph_xml("\u041e\u041e\u041e \u00ab\u041f\u0430\u0440\u0430\u043b\u043b\u0435\u043b\u044c\u043d\u044b\u0435 \u0420\u0435\u0448\u0435\u043d\u0438\u044f\u00bb \u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u0435\u0442 \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0442\u044b.", 24),
                    paragraph_xml("\u0421 \u0443\u0432\u0430\u0436\u0435\u043d\u0438\u0435\u043c,", 28),
                    paragraph_xml("\u041a.\u0418. \u041a\u0440\u0430\u0448\u0435\u043d\u0438\u043d\u043d\u0438\u043a\u043e\u0432", 28),
                ]
            )
            make_docx(source, document_xml(body))

            report = normalize_docx_for_preview(source, target, compact_body=True, max_body_font_half_points=20)
            root = read_document_xml(target)
            paragraphs = root.findall(f".//{qn(W_NS, 'p')}")

            body_size = paragraphs[2].find(f".//{qn(W_NS, 'sz')}").get(qn(W_NS, "val"))
            signature_size = paragraphs[3].find(f".//{qn(W_NS, 'sz')}").get(qn(W_NS, "val"))

            self.assertGreater(report.body_runs_compacted, 0)
            self.assertEqual(body_size, "20")
            self.assertEqual(signature_size, "28")


if __name__ == "__main__":
    unittest.main()
