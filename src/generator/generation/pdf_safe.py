from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf._page import PageObject
from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject

from src.generator.generation.document_builder import (
    BACKGROUND_ANCHOR_PATTERN,
    ANCHOR_EXTENT_PATTERN,
    SVG_EMBED_PATTERN,
    SVG_RELATION_PATTERN,
    flatten_svg_path,
    parse_svg_view_box,
    resolve_svg_fill_rule,
    resolve_svg_path_fill,
    remove_background_runs,
    svg_class_fills,
    _local_name,
)

EMU_PER_POINT = 12700
KP_PDF_BACKGROUND_POSITIONS_PT = (
    (405.0, -90.0),
    (205.0, -245.0),
)
KP_CONTACT_ICON_MAX_SIZE_PT = 20.0
KP_CONTACT_ICON_AUTHOR_LEFT_OFFSET_PT = 11.0
KP_CONTACT_ICON_TEXT_GAP_PT = 4.0
KP_CONTACT_ICON_PHONE_BASELINE_OFFSET_PT = 4.0
KP_CONTACT_ICON_EMAIL_BASELINE_OFFSET_PT = 1.0
KP_CONTACT_FALLBACK_ROW_GAP_PT = 12.2
KP_CONTACT_TEXT_LEADING_SPACES = ""
KP_CONTACT_PHONE_PARAGRAPH_AFTER_TWIPS = "60"
KP_CONTACT_TEXT_LINE_TOLERANCE_PT = 1.5
WORD_ANCHOR_PATTERN = re.compile(r"<wp:anchor\b[\s\S]*?</wp:anchor>", re.S)
WORD_DRAWING_PATTERN = re.compile(r"<w:drawing>[\s\S]*?</w:drawing>", re.S)


@dataclass(frozen=True)
class PdfSafePlan:
    source_docx: Path
    staged_docx: Path
    template_docx: Path | None = None
    should_overlay_kp_background: bool = False
    should_overlay_kp_contact_icons: bool = False


def prepare_docx_for_pdf_export(
    source_docx: Path,
    staged_docx: Path,
    *,
    file_kind: str | None = None,
    template_docx: Path | None = None,
    max_body_font_half_points: int = 20,
) -> PdfSafePlan:
    staged_docx.parent.mkdir(parents=True, exist_ok=True)
    is_kp = is_kp_docx(source_docx, file_kind=file_kind)
    if not is_kp:
        shutil.copy2(str(source_docx), str(staged_docx))
        return PdfSafePlan(source_docx=source_docx, staged_docx=staged_docx)

    can_overlay_contact_icons = len(extract_kp_contact_icons(source_docx)) >= 2
    copy_docx_without_pdf_unsafe_runs(
        source_docx,
        staged_docx,
        strip_contact_icons=can_overlay_contact_icons,
        max_body_font_half_points=max_body_font_half_points,
    )
    return PdfSafePlan(
        source_docx=source_docx,
        staged_docx=staged_docx,
        template_docx=template_docx if template_docx and template_docx.exists() else source_docx,
        should_overlay_kp_background=True,
        should_overlay_kp_contact_icons=can_overlay_contact_icons,
    )


def is_kp_docx(path: Path, *, file_kind: str | None = None) -> bool:
    if str(file_kind or "").strip().lower() == "kp":
        return True
    return path.name.casefold().startswith("\u043a\u043f_") or "_kp_" in path.name.casefold()


def copy_docx_without_pdf_unsafe_runs(
    source_docx: Path,
    target_docx: Path,
    *,
    strip_contact_icons: bool = False,
    max_body_font_half_points: int = 20,
) -> None:
    with zipfile.ZipFile(source_docx, "r") as source_zip:
        items = source_zip.infolist()
        payloads = {item.filename: source_zip.read(item.filename) for item in items}

    document_name = "word/document.xml"
    if document_name in payloads:
        document_text = payloads[document_name].decode("utf-8", errors="ignore")
        icons_changed = False
        if strip_contact_icons:
            document_text, icons_changed = remove_contact_icon_runs(document_text)
        document_text, background_changed = remove_background_runs(document_text)
        document_text, contact_text_changed = normalize_contact_text_for_pdf(document_text)
        document_text, body_font_changed = shrink_mngp_kp_body_for_pdf(
            document_text,
            max_body_font_half_points=max_body_font_half_points,
        )
        if background_changed or icons_changed or contact_text_changed or body_font_changed:
            payloads[document_name] = document_text.encode("utf-8")

    with zipfile.ZipFile(target_docx, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
        for item in items:
            target_zip.writestr(item, payloads[item.filename])


def shrink_mngp_kp_body_for_pdf(document_text: str, *, max_body_font_half_points: int = 20) -> tuple[str, bool]:
    """Make the KP body compact for PDF export.

    The signature/contact area uses anchored objects and provider-specific PDF
    postprocessing, so changing that area tends to move the stamp/contact block.
    """
    try:
        from lxml import etree
    except ImportError:
        return document_text, False

    try:
        root = etree.fromstring(document_text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return document_text, False

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = root.xpath(".//w:p", namespaces=ns)
    texts = ["".join(paragraph.xpath(".//w:t/text()", namespaces=ns)).strip() for paragraph in paragraphs]
    body_range = _find_kp_body_paragraph_range(texts)
    if body_range is None:
        return document_text, False
    start_index, end_index = body_range
    if end_index is None or end_index < start_index:
        return document_text, False

    changed = False
    size_value = str(max(14, min(24, int(max_body_font_half_points))))  # Word stores font size in half-points.
    for paragraph in paragraphs[start_index : end_index + 1]:
        ppr = paragraph.find("w:pPr", namespaces=ns)
        if ppr is None:
            ppr = etree.Element("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr")
            paragraph.insert(0, ppr)
            changed = True
        spacing = ppr.find("w:spacing", namespaces=ns)
        if spacing is None:
            spacing = etree.Element("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}spacing")
            ppr.append(spacing)
            changed = True
        for attr, value in (("before", "0"), ("after", "0")):
            key = f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{attr}"
            if spacing.get(key) != value:
                spacing.set(key, value)
                changed = True

        for run in paragraph.xpath(".//w:r[w:t]", namespaces=ns):
            rpr = run.find("w:rPr", namespaces=ns)
            if rpr is None:
                rpr = etree.Element("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rPr")
                run.insert(0, rpr)
                changed = True
            for tag in ("sz", "szCs"):
                node = rpr.find(f"w:{tag}", namespaces=ns)
                if node is None:
                    node = etree.Element(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{tag}")
                    rpr.append(node)
                    changed = True
                if node.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") != size_value:
                    node.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", size_value)
                    changed = True

    if not changed:
        return document_text, False
    return etree.tostring(root, encoding="unicode"), True


def _find_kp_body_paragraph_range(texts: list[str]) -> tuple[int, int] | None:
    lowered_texts = [text.casefold() for text in texts]
    full_text = "\n".join(lowered_texts)
    title_token = "\u043a\u043e\u043c\u043c\u0435\u0440\u0447\u0435\u0441\u043a\u043e\u0435 \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435"
    if title_token not in full_text:
        return None

    start_index = None
    for index, lowered in enumerate(lowered_texts):
        if (
            "\u043e\u043e\u043e \u00ab\u043f\u0430\u0440\u0430\u043b\u043b\u0435\u043b\u044c\u043d\u044b\u0435 \u0440\u0435\u0448\u0435\u043d\u0438\u044f\u00bb" in lowered
            and "\u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u0435\u0442 \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0442\u044b" in lowered
        ):
            start_index = index
            break

    if start_index is None:
        for index, lowered in enumerate(lowered_texts):
            if title_token in lowered:
                start_index = min(index + 1, len(texts) - 1)
                break

    if start_index is None:
        return None

    end_index = len(texts) - 1
    for index in range(start_index, len(texts)):
        lowered = lowered_texts[index].strip()
        if lowered.startswith("\u0441 \u0443\u0432\u0430\u0436\u0435\u043d\u0438\u0435\u043c"):
            end_index = index - 1
            break
    return start_index, end_index

def remove_contact_icon_runs(document_text: str) -> tuple[str, bool]:
    changed = False

    def replace_drawing(match: re.Match[str]) -> str:
        nonlocal changed
        drawing = match.group(0)
        if not _is_small_contact_drawing(drawing):
            return drawing
        changed = True
        return ""

    return WORD_DRAWING_PATTERN.sub(replace_drawing, document_text), changed


def normalize_contact_text_for_pdf(document_text: str) -> tuple[str, bool]:
    try:
        from lxml import etree
    except ImportError:
        return document_text, False

    try:
        root = etree.fromstring(document_text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return document_text, False

    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "xml": "http://www.w3.org/XML/1998/namespace",
    }
    changed = False
    for paragraph in root.xpath(".//w:p", namespaces=ns):
        text = "".join(paragraph.xpath(".//w:t/text()", namespaces=ns))
        normalized = text.casefold()
        is_phone = "\u0442\u0435\u043b." in normalized
        is_email = "ks" in normalized or "@parresh" in normalized or "parresh" in normalized
        if not is_phone and not is_email:
            continue

        for bold_node in paragraph.xpath(".//w:rPr/w:b", namespaces=ns):
            parent = bold_node.getparent()
            if parent is not None:
                parent.remove(bold_node)
                changed = True

        text_nodes = paragraph.xpath(".//w:t", namespaces=ns)
        for text_node in text_nodes:
            value = text_node.text or ""
            if not value.strip():
                if value:
                    text_node.text = ""
                    changed = True
                continue
            stripped = value.lstrip()
            desired = KP_CONTACT_TEXT_LEADING_SPACES + stripped
            if value != desired:
                text_node.text = desired
                text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                changed = True
            break

        if is_phone:
            ppr = paragraph.find("w:pPr", namespaces=ns)
            if ppr is None:
                ppr = etree.Element("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr")
                paragraph.insert(0, ppr)
                changed = True
            spacing = ppr.find("w:spacing", namespaces=ns)
            if spacing is None:
                spacing = etree.Element("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}spacing")
                ppr.append(spacing)
                changed = True
            if spacing.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}after") != KP_CONTACT_PHONE_PARAGRAPH_AFTER_TWIPS:
                spacing.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}after", KP_CONTACT_PHONE_PARAGRAPH_AFTER_TWIPS)
                changed = True

    if not changed:
        return document_text, False
    return etree.tostring(root, encoding="unicode"), True


def _is_small_contact_anchor(anchor: str) -> bool:
    return _is_small_contact_drawing(anchor)


def _is_small_contact_drawing(drawing: str) -> bool:
    extent_match = ANCHOR_EXTENT_PATTERN.search(drawing)
    if not extent_match:
        return False
    width_pt = int(extent_match.group("cx")) / EMU_PER_POINT
    height_pt = int(extent_match.group("cy")) / EMU_PER_POINT
    return width_pt <= KP_CONTACT_ICON_MAX_SIZE_PT and height_pt <= KP_CONTACT_ICON_MAX_SIZE_PT


def apply_pdf_safe_postprocess(pdf_path: Path, plan: PdfSafePlan) -> None:
    if not plan.should_overlay_kp_background:
        return
    source = plan.template_docx if plan.template_docx and plan.template_docx.exists() else plan.source_docx
    backgrounds = extract_kp_backgrounds(source) if plan.should_overlay_kp_background else []
    icons = extract_kp_contact_icons(plan.source_docx) if plan.should_overlay_kp_contact_icons else []
    if plan.should_overlay_kp_contact_icons and len(icons) < 2 and source != plan.source_docx:
        template_icons = extract_kp_contact_icons(source)
        if len(template_icons) > len(icons):
            icons = template_icons
    if not backgrounds and not icons:
        return
    overlay_kp_decorations(pdf_path, backgrounds, icons)


@dataclass(frozen=True)
class KpBackground:
    svg_payload: bytes
    width_pt: float
    height_pt: float


@dataclass(frozen=True)
class KpContactIcon:
    svg_payload: bytes
    width_pt: float
    height_pt: float


@dataclass(frozen=True)
class KpContactTextPositions:
    author_x: float | None = None
    phone_x: float | None = None
    phone_y: float | None = None
    email_y: float | None = None


def extract_kp_backgrounds(docx_path: Path) -> list[KpBackground]:
    if not docx_path.exists():
        return []
    try:
        with zipfile.ZipFile(docx_path, "r") as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names or "word/_rels/document.xml.rels" not in names:
                return []
            document_text = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            rels_text = archive.read("word/_rels/document.xml.rels").decode("utf-8", errors="ignore")
            targets = {match.group("id"): match.group("target") for match in SVG_RELATION_PATTERN.finditer(rels_text)}
            result: list[KpBackground] = []
            for anchor_match in BACKGROUND_ANCHOR_PATTERN.finditer(document_text):
                anchor = anchor_match.group(0)
                if _is_small_contact_anchor(anchor):
                    continue
                extent_match = ANCHOR_EXTENT_PATTERN.search(anchor)
                svg_match = SVG_EMBED_PATTERN.search(anchor)
                if not extent_match or not svg_match:
                    continue
                target = targets.get(svg_match.group("id"), "")
                if not target.lower().endswith(".svg"):
                    continue
                media_part = str((Path("word/document.xml").parent / target).as_posix())
                if media_part not in names:
                    continue
                result.append(
                    KpBackground(
                        svg_payload=archive.read(media_part),
                        width_pt=int(extent_match.group("cx")) / EMU_PER_POINT,
                        height_pt=int(extent_match.group("cy")) / EMU_PER_POINT,
                    )
                )
            return result
    except (OSError, zipfile.BadZipFile):
        return []


def extract_kp_contact_icons(docx_path: Path) -> list[KpContactIcon]:
    if not docx_path.exists():
        return []
    try:
        with zipfile.ZipFile(docx_path, "r") as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names or "word/_rels/document.xml.rels" not in names:
                return []
            document_text = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            rels_text = archive.read("word/_rels/document.xml.rels").decode("utf-8", errors="ignore")
            targets = {match.group("id"): match.group("target") for match in SVG_RELATION_PATTERN.finditer(rels_text)}
            result: list[KpContactIcon] = []
            for drawing in WORD_DRAWING_PATTERN.findall(document_text):
                if not _is_small_contact_drawing(drawing):
                    continue
                extent_match = ANCHOR_EXTENT_PATTERN.search(drawing)
                svg_match = SVG_EMBED_PATTERN.search(drawing)
                if not extent_match or not svg_match:
                    continue
                width_pt = int(extent_match.group("cx")) / EMU_PER_POINT
                height_pt = int(extent_match.group("cy")) / EMU_PER_POINT
                target = targets.get(svg_match.group("id"), "")
                if not target.lower().endswith(".svg"):
                    continue
                media_part = str((Path("word/document.xml").parent / target).as_posix())
                if media_part not in names:
                    continue
                result.append(
                    KpContactIcon(
                        svg_payload=archive.read(media_part),
                        width_pt=width_pt,
                        height_pt=height_pt,
                    )
                )
            return result[:2]
    except (OSError, zipfile.BadZipFile):
        return []


def overlay_kp_decorations(pdf_path: Path, backgrounds: list[KpBackground], icons: list[KpContactIcon]) -> None:
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        return
    target_page_index = len(reader.pages) - 1
    writer = PdfWriter(clone_from=reader)
    page = writer.pages[target_page_index]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    contact_positions = find_contact_text_positions(page)
    if backgrounds:
        background_overlay = build_kp_decorations_overlay_page(width, height, backgrounds, [], contact_positions)
        page.merge_page(background_overlay, over=False)
    if icons:
        icon_overlay = build_kp_decorations_overlay_page(width, height, [], icons, contact_positions)
        page.merge_page(icon_overlay, over=True)

    tmp_path = pdf_path.with_suffix(pdf_path.suffix + ".tmp")
    with tmp_path.open("wb") as handle:
        writer.write(handle)
    tmp_path.replace(pdf_path)


def build_kp_decorations_overlay_page(
    page_width: float,
    page_height: float,
    backgrounds: list[KpBackground],
    icons: list[KpContactIcon],
    contact_positions: KpContactTextPositions,
) -> PageObject:
    overlay = PageObject.create_blank_page(width=page_width, height=page_height)
    content_parts = ["q"]
    if backgrounds:
        content_parts.append("/GSbg gs")
        for index, background in enumerate(backgrounds):
            x, y = KP_PDF_BACKGROUND_POSITIONS_PT[min(index, len(KP_PDF_BACKGROUND_POSITIONS_PT) - 1)]
            content = svg_to_pdf_path_content(
                background.svg_payload,
                x=x,
                y=y,
                width=background.width_pt,
                height=background.height_pt,
            )
            if content:
                content_parts.append(content)
    icon_positions = resolve_contact_icon_positions(icons, contact_positions)
    if icon_positions:
        content_parts.append("/GSicon gs")
        for icon, (x, y) in zip(icons, icon_positions):
            content = svg_to_pdf_path_content(
                icon.svg_payload,
                x=x,
                y=y,
                width=icon.width_pt,
                height=icon.height_pt,
            )
            if content:
                content_parts.append(content)
    content_parts.append("Q")

    stream = DecodedStreamObject()
    stream.set_data(("\n".join(content_parts) + "\n").encode("ascii"))
    overlay[NameObject("/Contents")] = stream
    overlay[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/ExtGState"): DictionaryObject(
                {
                    NameObject("/GSbg"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/ExtGState"),
                            NameObject("/ca"): FloatObject(0.78),
                            NameObject("/CA"): FloatObject(0.78),
                        }
                    ),
                    NameObject("/GSicon"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/ExtGState"),
                            NameObject("/ca"): FloatObject(1.0),
                            NameObject("/CA"): FloatObject(1.0),
                        }
                    ),
                }
            )
        }
    )
    return overlay


def resolve_contact_icon_positions(
    icons: list[KpContactIcon],
    positions: KpContactTextPositions,
) -> list[tuple[float, float]]:
    if len(icons) < 2:
        return []
    phone_y = positions.phone_y
    email_y = positions.email_y
    if phone_y is None and email_y is not None:
        phone_y = email_y + KP_CONTACT_FALLBACK_ROW_GAP_PT
    if email_y is None and phone_y is not None:
        email_y = phone_y - KP_CONTACT_FALLBACK_ROW_GAP_PT
    if phone_y is None or email_y is None:
        return []
    widest_icon = max((icon.width_pt for icon in icons[:2]), default=9.0)
    icon_x = max(24.0, (positions.phone_x or 66.0) - widest_icon - KP_CONTACT_ICON_TEXT_GAP_PT)
    return [
        (icon_x, phone_y - KP_CONTACT_ICON_PHONE_BASELINE_OFFSET_PT),
        (icon_x, email_y - KP_CONTACT_ICON_EMAIL_BASELINE_OFFSET_PT),
    ]


def find_contact_text_positions(page: PageObject) -> KpContactTextPositions:
    fragments: list[tuple[str, float, float]] = []

    def visitor(text: str, cm, tm, _font_dict, _font_size) -> None:
        stripped = text.strip()
        if not stripped:
            return
        x, y = transform_pdf_text_origin(cm, tm)
        fragments.append((stripped, x, y))

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        return KpContactTextPositions()

    author_candidates: list[tuple[float, float]] = []
    phone_candidates: list[tuple[float, float]] = []
    email_candidates: list[tuple[float, float]] = []
    for line_fragments in group_pdf_text_fragments_by_line(fragments):
        ordered_fragments = sorted(line_fragments, key=lambda item: item[1])
        compact_text = re.sub(
            r"\s+",
            "",
            "".join(fragment_text for fragment_text, _x, _y in ordered_fragments),
        ).casefold()
        line_x = min(fragment_x for _text, fragment_x, _y in ordered_fragments)
        line_y = sum(fragment_y for _text, _x, fragment_y in ordered_fragments) / len(ordered_fragments)
        if "\u0438\u0441\u043f." in compact_text and "\u0447\u0435\u0440\u043a\u0430\u0448\u0438\u043d\u0430" in compact_text:
            author_candidates.append((line_x, line_y))
        if "\u0442\u0435\u043b." in compact_text:
            phone_candidates.append((line_x, line_y))
        if "@" in compact_text and ("parresh" in compact_text or compact_text.startswith("ks@")):
            email_candidates.append((line_x, line_y))

    author_x = min(author_candidates, key=lambda item: item[1])[0] if author_candidates else None
    phone_x = None
    phone_y = None
    if phone_candidates:
        phone_x, phone_y = min(phone_candidates, key=lambda item: item[1])
    email_y = None
    if email_candidates:
        if phone_y is not None:
            below_phone = [item for item in email_candidates if item[1] < phone_y]
            if below_phone:
                email_y = max(below_phone, key=lambda item: item[1])[1]
            else:
                email_y = min(email_candidates, key=lambda item: abs(item[1] - phone_y))[1]
        else:
            email_y = min(email_candidates, key=lambda item: item[1])[1]

    return KpContactTextPositions(
        author_x=author_x,
        phone_x=phone_x,
        phone_y=phone_y,
        email_y=email_y,
    )


def transform_pdf_text_origin(cm, tm) -> tuple[float, float]:
    """Return a text origin in page coordinates, including the current transform."""
    if len(cm) < 6 or len(tm) < 6:
        return float(tm[4]), float(tm[5])
    return (
        float(tm[4]) * float(cm[0]) + float(tm[5]) * float(cm[2]) + float(cm[4]),
        float(tm[4]) * float(cm[1]) + float(tm[5]) * float(cm[3]) + float(cm[5]),
    )


def group_pdf_text_fragments_by_line(
    fragments: list[tuple[str, float, float]],
) -> list[list[tuple[str, float, float]]]:
    """Group split PDF text callbacks into visual lines by their page-space baseline."""
    lines: list[list[tuple[str, float, float]]] = []
    for fragment in sorted(fragments, key=lambda item: (-item[2], item[1])):
        matching_line = next(
            (
                line
                for line in lines
                if abs(sum(item[2] for item in line) / len(line) - fragment[2])
                <= KP_CONTACT_TEXT_LINE_TOLERANCE_PT
            ),
            None,
        )
        if matching_line is None:
            lines.append([fragment])
        else:
            matching_line.append(fragment)
    return lines


def svg_to_pdf_path_content(svg_payload: bytes, *, x: float, y: float, width: float, height: float) -> str:
    try:
        from xml.etree import ElementTree

        root = ElementTree.fromstring(svg_payload)
        view_box = parse_svg_view_box(root.attrib.get("viewBox", ""))
    except Exception:
        return ""
    if view_box is None:
        return ""
    view_x, view_y, view_width, view_height = view_box
    if view_width <= 0 or view_height <= 0:
        return ""

    scale_x = width / view_width
    scale_y = height / view_height
    class_fills = svg_class_fills(root)
    parts: list[str] = []
    for element in root.iter():
        if _local_name(element) != "path":
            continue
        path_data = element.attrib.get("d", "")
        fill = resolve_svg_path_fill(element, class_fills)
        if not path_data or fill is None:
            continue
        r, g, b, alpha = fill
        if alpha <= 0:
            continue
        parts.append(f"{r / 255:.6f} {g / 255:.6f} {b / 255:.6f} rg")
        for subpath in flatten_svg_path(path_data):
            pdf_points = [
                (x + (point_x - view_x) * scale_x, y + height - (point_y - view_y) * scale_y)
                for point_x, point_y in subpath
            ]
            if len(pdf_points) < 3:
                continue
            start_x, start_y = pdf_points[0]
            parts.append(f"{start_x:.3f} {start_y:.3f} m")
            for point_x, point_y in pdf_points[1:]:
                parts.append(f"{point_x:.3f} {point_y:.3f} l")
            parts.append("h")
        parts.append("f*" if resolve_svg_fill_rule(element) == "evenodd" else "f")
    return "\n".join(parts)
