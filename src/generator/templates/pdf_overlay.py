from __future__ import annotations

import base64
import html
import io
import re
import statistics
from ctypes import byref, c_uint
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import TemplateCompileError


_FIELD_TOKEN_RE = re.compile(r"(?<![A-Z0-9_])([A-Z][A-Z0-9_]{2,})(?![A-Z0-9_])")
_OUTGOING_NUMBER_RE = re.compile(r"(?P<number>\d{1,8})\s*[-–—]?\s*\u041a\u041f", re.IGNORECASE)
_OUTGOING_DATE_RE = re.compile(r"\u041a\u041f\s+\u043e\u0442\s+(?P<date>\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)
_BROKEN_WORD_FIELD_RE = re.compile(r"Error!\s+Unknown document(?: property name\.)?", re.IGNORECASE)
_GREETING_RE = re.compile(
    "\u0423\u0432\u0430\u0436\u0430\u0435\u043c(?:\u044b\u0439|\u0430\u044f)"
    r"\s*\(\u0430\u044f\)\s+HEAD_FIO\s*!",
    re.IGNORECASE,
)



_TOKEN_FIELDS = {
    "ADM": "ADM_NAME_1",
    "ADM_NAME": "ADM_NAME_1",
    "ADM_NAME_1": "ADM_NAME_1",
    "HEAD_FIO": "HEAD_FIO_SHORT",
    "HEAD_FIO_1": "HEAD_FIO",
    "HEAD_FIO_SHORT": "HEAD_FIO_SHORT",
    "HEAD_GREETING": "HEAD_GREETING",
    "MUN_NAME": "MUN_NAME",
    "MUN_NAME_1": "MUN_NAME_1",
    "MUN_NAME_2": "MUN_NAME_2",
    "MUN_R_NAME": "MUN_R_NAME",
    "MUN_R_NAME_1": "MUN_R_NAME_1",
    "SUB_RF": "SUB_RF",
    "SUB_RF_1": "SUB_RF_1",
    "WORK_TITLE": "WORK_TITLE",
    "WORK_TITLE_1": "WORK_TITLE_1",
    "WORK_TITLE_NOMINATIVE": "WORK_TITLE_NOMINATIVE",
    "WORK_SHORT_NAME": "WORK_SHORT_NAME",
    "WORK_TYPE_LABEL": "WORK_TYPE_LABEL",
    "WORK_RESULT_NAME": "WORK_RESULT_NAME",
    "EMAIL_OSN": "EMAIL_OSN",
    "TEL_OSN": "TEL_OSN",
    "ADRES": "ADRES",
}

_REFERENCE_FIELDS = (
    "WORK_SCOPE_FRAGMENT",
    "HEAD_GREETING",
    "ADM_NAME_1",
    "ADM_NAME",
    "WORK_TITLE_1",
    "WORK_TITLE",
    "MUN_R_SCOPE_FRAGMENT",
    "MUN_R_NAME_1",
    "MUN_R_NAME",
    "MUN_NAME_2",
    "MUN_NAME_1",
    "MUN_NAME",
    "SUB_RF_1",
    "SUB_RF",
    "HEAD_FIO_SHORT",
    "HEAD_FIO",
    "EMAIL_OSN",
    "TEL_OSN",
    "ADRES",
)


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    fields: tuple[str, ...]
    template: str
    strategy: str
    confidence: float


@dataclass(frozen=True)
class _Overlay:
    candidate: _Candidate
    char_boxes: tuple[tuple[float, float, float, float], ...]
    left: float
    top: float
    width: float
    height: float
    first_line_indent: float
    font_family: str
    font_size: float
    font_weight: int
    color: str
    line_height: float
    min_font_size: float


def _pdfium_modules() -> tuple[Any, Any]:
    try:
        import pypdfium2 as pdfium
        from pypdfium2 import raw
    except ImportError as exc:  # pragma: no cover
        raise TemplateCompileError(
            "Для автоматической подготовки PDF-шаблона не установлен pypdfium2"
        ) from exc
    return pdfium, raw


def _normalize_with_map(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    indexes: list[int] = []
    in_space = False
    for index, char in enumerate(value):
        if char.isspace():
            if normalized and not in_space:
                normalized.append(" ")
                indexes.append(index)
            in_space = True
            continue
        normalized.append(char)
        indexes.append(index)
        in_space = False
    if normalized and normalized[-1] == " ":
        normalized.pop()
        indexes.pop()
    return "".join(normalized), indexes


def _to_source_span(start: int, end: int, mapping: list[int]) -> tuple[int, int]:
    return mapping[start], mapping[end - 1] + 1


def _find_candidates(text: str, context: dict[str, Any]) -> tuple[list[_Candidate], list[tuple[int, int]]]:
    normalized, mapping = _normalize_with_map(text)
    candidates: list[_Candidate] = []
    cleanup_ranges: list[tuple[int, int]] = []

    def add(start: int, end: int, fields: tuple[str, ...], template: str, strategy: str, confidence: float) -> None:
        source_start, source_end = _to_source_span(start, end, mapping)
        candidates.append(_Candidate(source_start, source_end, fields, template, strategy, confidence))

    for match in re.finditer(
        r"MUN_NAME_2\s+MUN_R_NAME(?:_1)?\s+SUB_RF(?:_1)?",
        normalized,
        flags=re.IGNORECASE,
    ):
        add(match.start(), match.end(), ("WORK_SCOPE_FRAGMENT",), "{{WORK_SCOPE_FRAGMENT}}", "legacy_work_scope", 1.0)
    for match in re.finditer(
        r"MUN_R_NAME(?:_1)?\s+SUB_RF(?:_1)?",
        normalized,
        flags=re.IGNORECASE,
    ):
        add(match.start(), match.end(), ("MUN_R_SCOPE_FRAGMENT",), "{{MUN_R_SCOPE_FRAGMENT}}", "legacy_district_scope", 1.0)

    for match in _GREETING_RE.finditer(normalized):
        add(
            match.start(),
            match.end(),
            ("HEAD_GREETING",),
            "{{HEAD_GREETING}}",
            "legacy_greeting_marker",
            1.02,
        )

    number_matches = list(_OUTGOING_NUMBER_RE.finditer(normalized))
    date_matches = list(_OUTGOING_DATE_RE.finditer(normalized))
    for number_match in number_matches:
        date_match = next(
            (item for item in date_matches if number_match.start() <= item.start() <= number_match.end() + 250),
            None,
        )
        if date_match is None:
            continue
        marker_start = normalized.rfind("№", max(0, number_match.start() - 250), number_match.start() + 1)
        if marker_start < 0:
            marker_start = number_match.start("number")
        end = date_match.end("date")
        while True:
            trailing = _BROKEN_WORD_FIELD_RE.match(normalized, end)
            if trailing is None:
                break
            end = trailing.end()
        add(
            marker_start, end, ("OUTGOING_NUMBER", "DATE"),
            "№ {{OUTGOING_NUMBER}}-КП от {{DATE}}", "combined_outgoing_line", 1.01,
        )

    if not any(item.strategy == "combined_outgoing_line" for item in candidates):
        for match in _OUTGOING_NUMBER_RE.finditer(normalized):
            start, end = match.span("number")
            add(start, end, ("OUTGOING_NUMBER",), "{{OUTGOING_NUMBER}}", "outgoing_number", 1.0)
        for match in _OUTGOING_DATE_RE.finditer(normalized):
            start, end = match.span("date")
            add(start, end, ("DATE",), "{{DATE}}", "outgoing_date", 1.0)

    for match in _BROKEN_WORD_FIELD_RE.finditer(normalized):
        cleanup_ranges.append(_to_source_span(match.start(), match.end(), mapping))

    for match in _FIELD_TOKEN_RE.finditer(normalized):
        field_name = _TOKEN_FIELDS.get(match.group(1).upper())
        if field_name:
            add(match.start(), match.end(), (field_name,), f"{{{{{field_name}}}}}", "legacy_token", 0.99)

    folded = normalized.casefold()
    for field_name in _REFERENCE_FIELDS:
        raw_value = str(context.get(field_name) or "").strip()
        if len(raw_value) < 4:
            continue
        normalized_value, _ = _normalize_with_map(raw_value)
        needle = normalized_value.casefold()
        start = 0
        while needle:
            found = folded.find(needle, start)
            if found < 0:
                break
            add(found, found + len(normalized_value), (field_name,), f"{{{{{field_name}}}}}", "reference_value", 0.94)
            start = found + len(normalized_value)

    selected: list[_Candidate] = []
    occupied: list[tuple[int, int]] = []
    for item in sorted(candidates, key=lambda value: (-value.confidence, -(value.end - value.start), value.start)):
        if any(item.start < end and item.end > start for start, end in occupied):
            continue
        selected.append(item)
        occupied.append((item.start, item.end))
    selected.sort(key=lambda item: item.start)

    grouped: list[_Candidate] = []
    index = 0
    while index < len(selected):
        current = selected[index]
        if index + 1 < len(selected):
            following = selected[index + 1]
            between = text[current.end : following.start]
            if (
                current.fields in {("WORK_TITLE",), ("WORK_TITLE_1",)}
                and following.fields in {("MUN_R_SCOPE_FRAGMENT",), ("WORK_SCOPE_FRAGMENT",)}
                and not between.strip()
            ):
                grouped.append(
                    _Candidate(
                        current.start,
                        following.end,
                        current.fields + following.fields,
                        current.template + " " + following.template,
                        "combined_work_scope",
                        min(current.confidence, following.confidence),
                    )
                )
                index += 2
                continue
        grouped.append(current)
        index += 1
    return grouped, cleanup_ranges


def _effective_font_size(reported_size: float, object_height: float, char_height: float) -> float:
    """Recover the visual font size when PDFium reports a transformed size.

    Some PDF producers encode text with a one-point font and scale the text
    matrix afterwards. PDFium then reports 1 from get_font_size() although the
    visible glyphs are ten or fourteen points high. In that case the glyph
    geometry is the reliable source. Arial-like fonts occupy roughly 74
    percent of their em square, hence the 1.35 conversion factor.
    """

    safe_reported = max(0.0, float(reported_size or 0.0))
    object_height = max(0.0, float(object_height or 0.0))
    glyph_height = object_height or max(0.0, float(char_height or 0.0)) * 0.75
    geometry_size = glyph_height * 1.35
    if glyph_height > 0 and (
        safe_reported < 4.0 or safe_reported < glyph_height * 0.55
    ):
        return max(5.5, geometry_size)
    return max(5.5, safe_reported or geometry_size)


def _object_style(page: Any, raw: Any, first_box: tuple[float, float, float, float]) -> tuple[str, float, int, str]:
    left, bottom, right, top = first_box
    best: tuple[float, Any] | None = None
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_TEXT]):
        try:
            obj_left, obj_bottom, obj_right, obj_top = obj.get_bounds()
        except Exception:
            continue
        overlap_x = max(0.0, min(right, obj_right) - max(left, obj_left))
        overlap_y = max(0.0, min(top, obj_top) - max(bottom, obj_bottom))
        score = overlap_x * overlap_y
        if score > 0 and (best is None or score > best[0]):
            best = (score, obj)
    if best is None:
        return "Arial", max(7.0, top - bottom), 400, "#000000"

    obj = best[1]
    try:
        font = obj.get_font()
        family = str(font.get_family_name() or "Arial")
        base_name = str(font.get_base_name() or "")
        raw_weight = int(font.get_weight() or 400)
        weight = 700 if "bold" in base_name.casefold() or raw_weight >= 600 else 400
    except Exception:
        family, weight = "Arial", 400
    try:
        obj_left, obj_bottom, obj_right, obj_top = obj.get_bounds()
        object_height = max(0.0, float(obj_top) - float(obj_bottom))
    except Exception:
        object_height = 0.0
    try:
        reported_size = float(obj.get_font_size() or 0.0)
    except Exception:
        reported_size = 0.0
    size = _effective_font_size(reported_size, object_height, top - bottom)
    red, green, blue, alpha = c_uint(0), c_uint(0), c_uint(0), c_uint(255)
    color = "#000000"
    try:
        if raw.FPDFPageObj_GetFillColor(obj.raw, byref(red), byref(green), byref(blue), byref(alpha)):
            color = f"#{red.value:02x}{green.value:02x}{blue.value:02x}"
    except Exception:
        pass
    return family, size, weight, color


def _line_groups(boxes: list[tuple[float, float, float, float]], font_size: float) -> list[list[tuple[float, float, float, float]]]:
    lines: list[list[tuple[float, float, float, float]]] = []
    for box in boxes:
        center = (box[1] + box[3]) / 2
        target = next(
            (line for line in lines if abs(center - statistics.mean((item[1] + item[3]) / 2 for item in line)) <= max(2.0, font_size * 0.45)),
            None,
        )
        if target is None:
            lines.append([box])
        else:
            target.append(box)
    return lines


def _overlay_for(page: Any, textpage: Any, raw: Any, candidate: _Candidate) -> _Overlay | None:
    text = textpage.get_text_range()
    indexed_boxes: list[tuple[int, tuple[float, float, float, float]]] = []
    for index in range(candidate.start, min(candidate.end, textpage.count_chars())):
        if text[index].isspace():
            continue
        try:
            box = tuple(float(value) for value in textpage.get_charbox(index, loose=True))
        except Exception:
            continue
        if box[2] > box[0] and box[3] > box[1]:
            indexed_boxes.append((index, box))
    if not indexed_boxes:
        return None

    boxes = [item[1] for item in indexed_boxes]
    family, font_size, weight, color = _object_style(page, raw, boxes[0])
    lines = _line_groups(boxes, font_size)
    line_centers = sorted((statistics.mean((item[1] + item[3]) / 2 for item in line) for line in lines), reverse=True)
    if len(line_centers) > 1:
        line_height = statistics.median(abs(a - b) for a, b in zip(line_centers, line_centers[1:]))
    else:
        line_height = font_size * 1.18

    page_width, page_height = page.get_size()
    source_left = min(box[0] for box in boxes)
    source_right = max(box[2] for box in boxes)
    source_bottom = min(box[1] for box in boxes)
    source_top = max(box[3] for box in boxes)
    first_left = boxes[0][0]
    fields = set(candidate.fields)
    multiline = len(lines) > 1 or bool(fields & {"ADM_NAME_1", "ADM_NAME", "HEAD_GREETING", "WORK_TITLE", "WORK_TITLE_1", "WORK_SCOPE_FRAGMENT", "MUN_R_SCOPE_FRAGMENT"})

    left = source_left
    right = source_right + 2
    height = source_top - source_bottom + 3
    indent = max(0.0, first_left - left)
    if fields & {"ADM_NAME_1", "ADM_NAME", "HEAD_GREETING"}:
        right = page_width - max(30.0, page_width * 0.06)
        height = max(height, line_height * 4.0)
        indent = 0.0
    elif fields == {"OUTGOING_NUMBER", "DATE"}:
        right = min(page_width - 25.0, left + page_width * 0.46)
        height = line_height * 1.4
        indent = 0.0
    elif fields & {"WORK_TITLE", "WORK_TITLE_1", "WORK_SCOPE_FRAGMENT", "MUN_R_SCOPE_FRAGMENT"}:
        in_table = source_bottom < page_height * 0.55 and source_bottom > page_height * 0.35
        right = page_width * 0.795 if in_table else page_width - max(25.0, page_width * 0.055)
        right = max(right, source_right + 2)
        if in_table:
            height = min(height, line_height * 2.25)
        else:
            height = max(height, line_height * (3.0 if multiline else 1.25))
    elif fields & {"HEAD_FIO", "HEAD_FIO_SHORT", "EMAIL_OSN", "TEL_OSN", "ADRES"}:
        right = min(page_width - 25.0, max(source_right + 6.0, left + page_width * 0.32))
        height = max(height, line_height * 1.25)

    top = page_height - source_top - 1.0
    return _Overlay(
        candidate=candidate,
        char_boxes=tuple(boxes),
        left=max(0.0, left - 0.5),
        top=max(0.0, top),
        width=max(4.0, right - left + 1.0),
        height=max(line_height, height),
        first_line_indent=indent,
        font_family=family,
        font_size=font_size,
        font_weight=weight,
        color=color,
        line_height=max(font_size, line_height),
        min_font_size=max(5.5, min(font_size * 0.68, 9.0)),
    )


def _median_ring_color(image: Any, rect: tuple[int, int, int, int]) -> tuple[int, int, int]:
    left, top, right, bottom = rect
    width, height = image.size
    samples: list[tuple[int, int, int]] = []
    for y in range(max(0, top - 2), min(height, bottom + 3)):
        for x in range(max(0, left - 2), min(width, right + 3)):
            if left <= x < right and top <= y < bottom:
                continue
            samples.append(tuple(image.getpixel((x, y))[:3]))
    if not samples:
        return (255, 255, 255)
    return tuple(int(statistics.median(channel)) for channel in zip(*samples))


def _clean_background(page: Any, overlays: list[_Overlay], cleanup_boxes: list[tuple[float, float, float, float]], scale: float) -> str:
    from PIL import ImageDraw

    image = page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
    page_height = page.get_size()[1]
    draw = ImageDraw.Draw(image)
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue = pixels[x, y]
            if red >= 225 and green >= 220 and blue <= 150:
                pixels[x, y] = (255, 255, 255)
    boxes = [box for overlay in overlays for box in overlay.char_boxes] + cleanup_boxes
    for left, bottom, right, top in boxes:
        pixel_box = (
            max(0, int(left * scale) - 3),
            max(0, int((page_height - top) * scale) - 3),
            min(image.width, int(right * scale) + 4),
            min(image.height, int((page_height - bottom) * scale) + 4),
        )
        draw.rectangle(pixel_box, fill=_median_ring_color(image, pixel_box))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _cleanup_boxes(textpage: Any, ranges: list[tuple[int, int]]) -> list[tuple[float, float, float, float]]:
    text = textpage.get_text_range()
    result: list[tuple[float, float, float, float]] = []
    for start, end in ranges:
        for index in range(start, min(end, textpage.count_chars())):
            if text[index].isspace():
                continue
            try:
                result.append(tuple(float(value) for value in textpage.get_charbox(index, loose=True)))
            except Exception:
                pass
    return result


def _foreground_images(page: Any, raw: Any, overlays: list[_Overlay]) -> list[dict[str, Any]]:
    overlay_boxes = [box for overlay in overlays for box in overlay.char_boxes]
    result: list[dict[str, Any]] = []
    for image_obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE]):
        try:
            left, bottom, right, top = (float(value) for value in image_obj.get_bounds())
        except Exception:
            continue
        if not any(min(right, box[2]) > max(left, box[0]) and min(top, box[3]) > max(bottom, box[1]) for box in overlay_boxes):
            continue
        try:
            image = image_obj.get_bitmap(render=True, scale_to_original=True).to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
        except Exception:
            continue
        result.append(
            {
                "left": left,
                "top": page.get_size()[1] - top,
                "width": right - left,
                "height": top - bottom,
                "src": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            }
        )
    return result


def _font_stack(family: str) -> str:
    escaped = family.replace("\\", "").replace('"', "")
    fallbacks = '"Carlito", "Liberation Sans", Arial, sans-serif' if "calibri" in escaped.casefold() else '"Liberation Sans", Arial, sans-serif'
    return f'"{escaped}", {fallbacks}'


def _build_html(page: Any, background: str, overlays: list[_Overlay], foreground: list[dict[str, Any]]) -> str:
    width, height = page.get_size()
    overlay_html: list[str] = []
    for index, overlay in enumerate(overlays):
        style = (
            f"left:{overlay.left:.3f}pt;top:{overlay.top:.3f}pt;width:{overlay.width:.3f}pt;"
            f"height:{overlay.height:.3f}pt;font-family:{_font_stack(overlay.font_family)};"
            f"font-size:{overlay.font_size:.3f}pt;font-weight:{overlay.font_weight};"
            f"color:{overlay.color};line-height:{overlay.line_height:.3f}pt;text-indent:{overlay.first_line_indent:.3f}pt;"
        )
        fields = ",".join(overlay.candidate.fields)
        overlay_html.append(
            f'<div class="adaptive-overlay" data-adaptive-container="{html.escape(fields)}" '
            f'data-min-font-size="{overlay.min_font_size:.3f}" data-overlay-index="{index}" '
            f'style="{style}">{overlay.candidate.template}</div>'
        )
    foreground_html = "".join(
        f'<img class="foreground" alt="" src="{item["src"]}" style="left:{item["left"]:.3f}pt;'
        f'top:{item["top"]:.3f}pt;width:{item["width"]:.3f}pt;height:{item["height"]:.3f}pt">'
        for item in foreground
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><style>
@page {{ size: {width:.3f}pt {height:.3f}pt; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; width: {width:.3f}pt; height: {height:.3f}pt; overflow: hidden; }}
.page {{ position: relative; width: {width:.3f}pt; height: {height:.3f}pt; overflow: hidden; background: white; }}
.background {{ position: absolute; inset: 0; width: 100%; height: 100%; display: block; }}
.adaptive-overlay {{ position: absolute; z-index: 2; overflow: hidden; white-space: normal; text-align: left; overflow-wrap: normal; word-break: normal; hyphens: none; }}
.adaptive-overlay [data-adaptive-field] {{ font: inherit; color: inherit; line-height: inherit; }}
.foreground {{ position: absolute; z-index: 3; object-fit: fill; }}
</style></head><body><main class="page">
<img class="background" alt="" src="{background}">
{''.join(overlay_html)}{foreground_html}
</main></body></html>"""


def build_pdf_overlay_html(source_path: Path, reference_context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Compile a PDF page into immutable artwork plus adaptive text overlays."""

    pdfium, raw = _pdfium_modules()
    try:
        document = pdfium.PdfDocument(source_path)
    except Exception as exc:
        raise TemplateCompileError("Не удалось открыть PDF-шаблон") from exc
    discarded_pages = 0
    if len(document) > 1:
        trailing_text = [document[index].get_textpage().get_text_range().strip() for index in range(1, len(document))]
        # Word can create a second page containing only a repeated letterhead
        # after a tiny layout overflow. It is safe to discard that tail, but a
        # real multi-page document must never be truncated silently.
        if all(len(value) <= 500 for value in trailing_text):
            discarded_pages = len(trailing_text)
        else:
            raise TemplateCompileError("Коммерческое предложение должно помещаться на одну страницу")
    page = document[0]
    textpage = page.get_textpage()
    text = textpage.get_text_range()
    if not text.strip():
        raise TemplateCompileError("В PDF нет текстового слоя. Для сканированного шаблона сначала требуется OCR/ИИ-распознавание.")
    candidates, cleanup_ranges = _find_candidates(text, reference_context)
    overlays = [overlay for candidate in candidates if (overlay := _overlay_for(page, textpage, raw, candidate))]
    if not overlays:
        raise TemplateCompileError("Не удалось определить изменяемые данные PDF. Загрузите таблицу с примером данных вместе с шаблоном.")
    cleanup = _cleanup_boxes(textpage, cleanup_ranges)
    background = _clean_background(page, overlays, cleanup, scale=2.5)
    foreground = _foreground_images(page, raw, overlays)
    compiled = _build_html(page, background, overlays, foreground)
    fields: dict[str, int] = {}
    strategies: dict[str, int] = {}
    for overlay in overlays:
        for field in overlay.candidate.fields:
            fields[field] = fields.get(field, 0) + 1
        strategies[overlay.candidate.strategy] = strategies.get(overlay.candidate.strategy, 0) + 1
    return compiled, {
        "mode": "visual_overlay_html",
        "engine": "pdfium",
        "fields": fields,
        "strategies": strategies,
        "background_scale": 2.5,
        "foreground_layers": len(foreground),
        "discarded_overflow_pages": discarded_pages,
        "reference_context_used": bool(reference_context),
        "regions": [
            {"fields": list(overlay.candidate.fields), "box": [overlay.left, overlay.top, overlay.width, overlay.height], "font_size": overlay.font_size}
            for overlay in overlays
        ],
    }
