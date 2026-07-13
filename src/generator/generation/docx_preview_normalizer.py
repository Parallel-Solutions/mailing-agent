from __future__ import annotations

import re
import shutil
import zipfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


@dataclass
class DocxPreviewNormalizationReport:
    source_docx: str
    target_docx: str
    foreground_anchors_inlined: int = 0
    foreground_anchors_normalized: int = 0
    background_anchors_kept: int = 0
    anchors_sent_behind_text: int = 0
    signature_tables_normalized: int = 0
    signature_icons_removed: int = 0
    signature_stamps_inlined: int = 0
    body_runs_compacted: int = 0
    body_paragraphs_compacted: int = 0
    processed_parts: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(
            (
                self.foreground_anchors_inlined,
                self.foreground_anchors_normalized,
                self.background_anchors_kept,
                self.anchors_sent_behind_text,
                self.signature_tables_normalized,
                self.signature_icons_removed,
                self.signature_stamps_inlined,
                self.body_runs_compacted,
                self.body_paragraphs_compacted,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_docx": self.source_docx,
            "target_docx": self.target_docx,
            "changed": self.changed,
            "foreground_anchors_inlined": self.foreground_anchors_inlined,
            "foreground_anchors_normalized": self.foreground_anchors_normalized,
            "background_anchors_kept": self.background_anchors_kept,
            "anchors_sent_behind_text": self.anchors_sent_behind_text,
            "signature_tables_normalized": self.signature_tables_normalized,
            "signature_icons_removed": self.signature_icons_removed,
            "signature_stamps_inlined": self.signature_stamps_inlined,
            "body_runs_compacted": self.body_runs_compacted,
            "body_paragraphs_compacted": self.body_paragraphs_compacted,
            "processed_parts": self.processed_parts,
        }


def normalize_docx_for_preview(
    source_docx: Path,
    target_docx: Path,
    *,
    compact_body: bool = False,
    max_body_font_half_points: int = 20,
) -> DocxPreviewNormalizationReport:
    report = DocxPreviewNormalizationReport(source_docx=str(source_docx), target_docx=str(target_docx))
    if not source_docx.exists() or source_docx.suffix.lower() != ".docx":
        shutil.copy2(str(source_docx), str(target_docx))
        return report

    target_docx.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source_docx, "r") as source_zip:
        items = source_zip.infolist()
        payloads = {item.filename: source_zip.read(item.filename) for item in items}

    for part_name in list(payloads):
        if not _is_word_xml_part(part_name):
            continue
        normalized = _normalize_xml_part(
            payloads[part_name],
            report=report,
            compact_body=compact_body,
            max_body_font_half_points=max_body_font_half_points,
        )
        if normalized is not None:
            payloads[part_name] = normalized
            report.processed_parts.append(part_name)

    with zipfile.ZipFile(target_docx, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
        for item in items:
            target_zip.writestr(item, payloads[item.filename])
    return report


def _is_word_xml_part(part_name: str) -> bool:
    lower = part_name.lower()
    if not lower.startswith("word/") or not lower.endswith(".xml"):
        return False
    name = Path(lower).name
    return name == "document.xml" or name.startswith("header") or name.startswith("footer")


def _normalize_xml_part(
    payload: bytes,
    *,
    report: DocxPreviewNormalizationReport,
    compact_body: bool,
    max_body_font_half_points: int,
) -> bytes | None:
    try:
        parser = etree.XMLParser(remove_blank_text=False, recover=True)
        root = etree.fromstring(payload, parser=parser)
    except etree.XMLSyntaxError:
        return None

    changed = False

    # Floating drawings are part of the template layout. Rewriting behindDoc,
    # allowOverlap or layoutInCell changes Word's z-order and can corrupt
    # translucent page decorations during LibreOffice PDF conversion. Compact
    # only the textual body and leave logos, icons, stamps and backgrounds byte
    # for byte as the template author positioned them.

    if compact_body:
        runs, paragraphs = _compact_main_body(root, max_body_font_half_points=max_body_font_half_points)
        if runs or paragraphs:
            report.body_runs_compacted += runs
            report.body_paragraphs_compacted += paragraphs
            changed = True

    if not changed:
        return None
    return etree.tostring(root, encoding="utf-8", xml_declaration=True, standalone=False)


def _normalize_signature_tables(root: etree._Element, report: DocxPreviewNormalizationReport) -> bool:
    changed = False
    for table in list(root.iter(_qn(W_NS, "tbl"))):
        table_text = _paragraph_text(table).casefold()
        if "\u0441 \u0443\u0432\u0430\u0436\u0435\u043d\u0438\u0435\u043c" not in table_text or "\u043a\u0440\u0430\u0448\u0435\u043d\u0438\u043d\u043d\u0438\u043a\u043e\u0432" not in table_text:
            continue

        rows = table.findall(_qn(W_NS, "tr"))
        if len(rows) < 2:
            continue
        heading_cells = rows[0].findall(_qn(W_NS, "tc"))
        detail_cells = rows[1].findall(_qn(W_NS, "tc"))
        if len(heading_cells) < 3 or len(detail_cells) < 3:
            continue

        table_changed = False
        left_cell = detail_cells[0]
        center_cell = detail_cells[1]
        contact_text = _normalize_spaces(_paragraph_text(left_cell))
        stamp_anchor = _largest_non_background_anchor(center_cell)

        icon_count = len(list(left_cell.iter(_qn(WP_NS, "anchor"))))
        if icon_count:
            _rebuild_contact_cell(left_cell, contact_text)
            report.signature_icons_removed += icon_count
            table_changed = True

        if stamp_anchor is not None:
            inline = _anchor_to_inline(stamp_anchor)
            if inline is not None:
                _rebuild_image_cell(center_cell, inline)
                report.signature_stamps_inlined += 1
                table_changed = True

        if table_changed:
            report.signature_tables_normalized += 1
            changed = True
    return changed


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _clear_cell_keep_properties(cell: etree._Element) -> None:
    for child in list(cell):
        if child.tag == _qn(W_NS, "tcPr"):
            continue
        cell.remove(child)


def _first_run_properties(element: etree._Element) -> etree._Element | None:
    run_properties = element.find(f".//{_qn(W_NS, 'rPr')}")
    return deepcopy(run_properties) if run_properties is not None else None


def _append_text_paragraph(cell: etree._Element, text: str, run_properties: etree._Element | None = None) -> None:
    paragraph = etree.Element(_qn(W_NS, "p"))
    run = etree.SubElement(paragraph, _qn(W_NS, "r"))
    if run_properties is not None:
        run.append(deepcopy(run_properties))
    text_node = etree.SubElement(run, _qn(W_NS, "t"))
    text_node.text = text
    cell.append(paragraph)


def _strip_phone_prefix(value: str) -> str:
    return re.sub(r"^\s*\u0442\u0435\u043b\.?\s*", "", value, flags=re.IGNORECASE).strip()


def _rebuild_contact_cell(cell: etree._Element, contact_text: str) -> None:
    run_properties = _first_run_properties(cell)
    phone_match = re.search(r"(\u0442\u0435\u043b\.?\s*\+?[0-9 ()\-\u00a0]+)", contact_text, flags=re.IGNORECASE)
    email_match = re.search(r"[\w.+-]+@[\w.-]+", contact_text)

    phone = _normalize_spaces(phone_match.group(1)) if phone_match else "+7 921 409-45-61"
    email = email_match.group(0) if email_match else "ks@parresh.ru"
    performer = contact_text
    if phone_match:
        performer = performer[: phone_match.start()]
    elif email_match:
        performer = performer[: email_match.start()]
    performer = _normalize_spaces(performer) or "\u0418\u0441\u043f. \u041a\u0440\u0430\u0448\u0435\u043d\u0438\u043d\u043d\u0438\u043a\u043e\u0432 \u041a\u043e\u043d\u0441\u0442\u0430\u043d\u0442\u0438\u043d"

    _clear_cell_keep_properties(cell)
    _append_text_paragraph(cell, performer, run_properties)
    _append_text_paragraph(cell, f"\u0442\u0435\u043b. {_strip_phone_prefix(phone)}", run_properties)
    _append_text_paragraph(cell, email, run_properties)


def _largest_non_background_anchor(element: etree._Element) -> etree._Element | None:
    candidates = []
    for anchor in element.iter(_qn(WP_NS, "anchor")):
        if _is_large_background_anchor(anchor):
            continue
        extent = anchor.find(_qn(WP_NS, "extent"))
        if extent is None:
            continue
        try:
            area = int(extent.get("cx") or "0") * int(extent.get("cy") or "0")
        except ValueError:
            area = 0
        candidates.append((area, anchor))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _rebuild_image_cell(cell: etree._Element, inline: etree._Element) -> None:
    _clear_cell_keep_properties(cell)
    paragraph = etree.Element(_qn(W_NS, "p"))
    ppr = etree.SubElement(paragraph, _qn(W_NS, "pPr"))
    jc = etree.SubElement(ppr, _qn(W_NS, "jc"))
    jc.set(_qn(W_NS, "val"), "center")
    run = etree.SubElement(paragraph, _qn(W_NS, "r"))
    drawing = etree.SubElement(run, _qn(W_NS, "drawing"))
    drawing.append(inline)
    cell.append(paragraph)


def _is_large_background_anchor(anchor: etree._Element) -> bool:
    extent = anchor.find(_qn(WP_NS, "extent"))
    if extent is None:
        return False
    try:
        cx = int(extent.get("cx") or "0")
        cy = int(extent.get("cy") or "0")
    except ValueError:
        return False
    # Keep only truly decorative page-scale backgrounds floating and behind text.
    is_large = cx >= 3_800_000 or cy >= 3_800_000
    return is_large and str(anchor.get("behindDoc") or "").lower() in {"1", "true"}



def _anchor_to_inline(anchor: etree._Element) -> etree._Element | None:
    graphic = anchor.find(_qn(A_NS, "graphic"))
    if graphic is None:
        return None

    inline = etree.Element(_qn(WP_NS, "inline"), nsmap=anchor.nsmap)
    for attr_name in ("distT", "distB", "distL", "distR"):
        if anchor.get(attr_name) is not None:
            inline.set(attr_name, anchor.get(attr_name) or "0")

    for child_tag in ("extent", "effectExtent", "docPr", "cNvGraphicFramePr"):
        child = anchor.find(_qn(WP_NS, child_tag))
        if child is not None:
            inline.append(deepcopy(child))
    inline.append(deepcopy(graphic))
    return inline


def _compact_main_body(root: etree._Element, *, max_body_font_half_points: int) -> tuple[int, int]:
    paragraphs = list(root.iter(_qn(W_NS, "p")))
    if not paragraphs:
        return 0, 0

    texts = [_paragraph_text(paragraph).strip() for paragraph in paragraphs]
    start_index = _find_body_start(texts)
    end_index = _find_body_end(texts, start_index)
    if start_index is None or end_index is None or end_index < start_index:
        return 0, 0

    changed_runs = 0
    changed_paragraphs = 0
    for paragraph in paragraphs[start_index : end_index + 1]:
        if _compact_paragraph_spacing(paragraph):
            changed_paragraphs += 1
        for run in paragraph.iter(_qn(W_NS, "r")):
            if _compact_run_font(run, max_body_font_half_points=max_body_font_half_points):
                changed_runs += 1
    return changed_runs, changed_paragraphs


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(_qn(W_NS, "t")))


def _find_body_start(texts: list[str]) -> int | None:
    for index, text in enumerate(texts):
        lowered = text.casefold()
        if "\u043a\u043e\u043c\u043c\u0435\u0440\u0447\u0435\u0441\u043a\u043e\u0435 \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435" in lowered:
            return min(index + 1, len(texts) - 1)
        if "\u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u0435\u0442 \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0442\u044b" in lowered:
            return index
    return 0 if texts else None


def _find_body_end(texts: list[str], start_index: int | None) -> int | None:
    if start_index is None:
        return None
    for index in range(start_index, len(texts)):
        lowered = texts[index].casefold()
        if lowered.startswith("\u0441 \u0443\u0432\u0430\u0436\u0435\u043d\u0438\u0435\u043c"):
            return max(start_index, index - 1)
    return len(texts) - 1


def _compact_paragraph_spacing(paragraph: etree._Element) -> bool:
    ppr = paragraph.find(_qn(W_NS, "pPr"))
    if ppr is None:
        ppr = etree.Element(_qn(W_NS, "pPr"))
        paragraph.insert(0, ppr)
    spacing = ppr.find(_qn(W_NS, "spacing"))
    if spacing is None:
        spacing = etree.Element(_qn(W_NS, "spacing"))
        ppr.append(spacing)

    changed = False
    for key, value in {
        _qn(W_NS, "before"): "0",
        _qn(W_NS, "after"): "40",
        _qn(W_NS, "line"): "220",
        _qn(W_NS, "lineRule"): "auto",
    }.items():
        if spacing.get(key) != value:
            spacing.set(key, value)
            changed = True
    return changed


def _compact_run_font(run: etree._Element, *, max_body_font_half_points: int) -> bool:
    if not _paragraph_text(run).strip():
        return False
    rpr = run.find(_qn(W_NS, "rPr"))
    if rpr is None:
        rpr = etree.Element(_qn(W_NS, "rPr"))
        run.insert(0, rpr)

    changed = False
    for tag in ("sz", "szCs"):
        size = rpr.find(_qn(W_NS, tag))
        if size is None:
            size = etree.Element(_qn(W_NS, tag))
            rpr.append(size)
            size.set(_qn(W_NS, "val"), str(max_body_font_half_points))
            changed = True
            continue
        try:
            current = int(size.get(_qn(W_NS, "val")) or "0")
        except ValueError:
            current = 0
        if current <= 0 or current > max_body_font_half_points:
            size.set(_qn(W_NS, "val"), str(max_body_font_half_points))
            changed = True
    return changed
