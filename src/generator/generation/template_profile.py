from __future__ import annotations

import re
import statistics
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP_NS = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
MIN_BODY_FONT_PT = 7.5
MAX_BODY_FONT_PT = 14.0


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _xml_root(payload: bytes) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return None


def _attr(element: ElementTree.Element | None, name: str) -> str:
    if element is None:
        return ""
    return _safe_text(element.attrib.get(f"{W_NS}val"))


def _hex_color(value: str) -> str:
    value = _safe_text(value).lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return ""
    return f"#{value.upper()}"


def _most_common(counter: Counter[str], fallback: str = "") -> str:
    if not counter:
        return fallback
    return counter.most_common(1)[0][0] or fallback


def _median_font_size(values: list[float], fallback: float) -> float:
    filtered = [value for value in values if MIN_BODY_FONT_PT <= value <= MAX_BODY_FONT_PT]
    if not filtered:
        return fallback
    return round(float(statistics.median(filtered)), 1)


def _run_profile(run: ElementTree.Element) -> dict[str, Any]:
    rpr = run.find(f"{W_NS}rPr")
    if rpr is None:
        return {}

    profile: dict[str, Any] = {}
    fonts = rpr.find(f"{W_NS}rFonts")
    if fonts is not None:
        for key in ("ascii", "hAnsi", "cs", "eastAsia"):
            font_name = _safe_text(fonts.attrib.get(f"{W_NS}{key}"))
            if font_name:
                profile["font"] = font_name
                break

    size = _attr(rpr.find(f"{W_NS}sz"), "val")
    if size:
        try:
            profile["font_size_pt"] = int(size) / 2
        except ValueError:
            pass

    color = _hex_color(_attr(rpr.find(f"{W_NS}color"), "val"))
    if color:
        profile["color"] = color

    highlight = _attr(rpr.find(f"{W_NS}highlight"), "val")
    if highlight:
        profile["highlight"] = highlight

    if rpr.find(f"{W_NS}b") is not None:
        profile["bold"] = True
    if rpr.find(f"{W_NS}i") is not None:
        profile["italic"] = True
    return profile


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{W_NS}t":
            parts.append(node.text or "")
        elif node.tag == f"{W_NS}tab":
            parts.append("\t")
        elif node.tag == f"{W_NS}br":
            parts.append("\n")
    return " ".join("".join(parts).split())


def _count_images(root: ElementTree.Element) -> dict[str, int]:
    anchors = list(root.iter(f"{WP_NS}anchor"))
    inline = list(root.iter(f"{WP_NS}inline"))
    background = 0
    foreground = 0
    for anchor in anchors:
        if _safe_text(anchor.attrib.get("behindDoc")) == "1":
            background += 1
        else:
            foreground += 1
    return {
        "anchored_background": background,
        "anchored_foreground": foreground,
        "inline": len(inline),
    }


def analyze_docx_style_profile(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists() or path.suffix.lower() != ".docx":
        return {"available": False, "reason": "not_docx"}

    fonts: Counter[str] = Counter()
    colors: Counter[str] = Counter()
    highlights: Counter[str] = Counter()
    alignments: Counter[str] = Counter()
    font_sizes: list[float] = []
    title_sizes: list[float] = []
    paragraph_count = 0
    table_count = 0
    image_count = 0
    image_layout = {"anchored_background": 0, "anchored_foreground": 0, "inline": 0}
    bold_runs = 0
    italic_runs = 0

    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            image_count = sum(1 for name in names if name.startswith("word/media/"))
            part_names = [
                name
                for name in names
                if name == "word/document.xml"
                or re.match(r"word/(header|footer)\d+\.xml$", name)
            ]
            for part_name in part_names:
                root = _xml_root(archive.read(part_name))
                if root is None:
                    continue
                counts = _count_images(root)
                for key, value in counts.items():
                    image_layout[key] += value
                for paragraph in root.iter(f"{W_NS}p"):
                    if _paragraph_text(paragraph):
                        paragraph_count += 1
                    ppr = paragraph.find(f"{W_NS}pPr")
                    if ppr is not None:
                        alignment = _attr(ppr.find(f"{W_NS}jc"), "val")
                        if alignment:
                            alignments[alignment] += 1
                    for run in paragraph.findall(f"{W_NS}r"):
                        profile = _run_profile(run)
                        if not profile:
                            continue
                        if profile.get("font"):
                            fonts[str(profile["font"])] += 1
                        if profile.get("color"):
                            colors[str(profile["color"])] += 1
                        if profile.get("highlight"):
                            highlights[str(profile["highlight"])] += 1
                        if profile.get("bold"):
                            bold_runs += 1
                        if profile.get("italic"):
                            italic_runs += 1
                        size = profile.get("font_size_pt")
                        if isinstance(size, (int, float)):
                            font_sizes.append(float(size))
                            if float(size) > 12:
                                title_sizes.append(float(size))
                table_count += sum(1 for _ in root.iter(f"{W_NS}tbl"))
    except (OSError, zipfile.BadZipFile) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    body_size = _median_font_size(font_sizes, 10.0)
    title_size = round(max(title_sizes), 1) if title_sizes else round(min(body_size + 1.8, 14.0), 1)
    primary_color = _most_common(colors, "#17213F")
    accent_color = "#4A9E1F"
    if primary_color == "#17213F" and len(colors) > 1:
        accent_color = colors.most_common(2)[-1][0]

    risks: list[str] = []
    if image_layout["anchored_foreground"] or image_layout["inline"]:
        risks.append("template_has_foreground_or_inline_images")
    if table_count == 0:
        risks.append("template_has_no_tables")
    if paragraph_count == 0:
        risks.append("template_has_no_readable_paragraphs")
    if image_count > 6:
        risks.append("template_has_many_images")

    return {
        "available": True,
        "source": path.name,
        "format": "docx",
        "font_family": _most_common(fonts, "Arial"),
        "body_font_size_pt": body_size,
        "title_font_size_pt": title_size,
        "primary_color": primary_color,
        "accent_color": accent_color,
        "highlight_colors": sorted(highlights),
        "alignment_counts": dict(alignments),
        "table_count": table_count,
        "paragraph_count": paragraph_count,
        "image_count": image_count,
        "image_layout": image_layout,
        "bold_run_count": bold_runs,
        "italic_run_count": italic_runs,
        "layout_risks": risks,
        "normalization": {
            "renderer": "docx_template_pdf_fit",
            "images": "preserve_template_layout",
            "page_count": "one_page_required",
            "overflow": "compact_pdf_export_only",
            "tables": "preserve_template_tables",
        },
    }