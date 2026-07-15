from __future__ import annotations

import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from .base import TemplateCompileError
from .protocol import PLACEHOLDER_RE


class _MalformedCMapNoiseFilter(logging.Filter):
    _PREFIXES = ("Skipping broken line", "Got invalid hex string")

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.getMessage()).startswith(self._PREFIXES)


_PYPDF_CMAP_LOGGER = logging.getLogger("pypdf._cmap")
if not any(isinstance(item, _MalformedCMapNoiseFilter) for item in _PYPDF_CMAP_LOGGER.filters):
    _PYPDF_CMAP_LOGGER.addFilter(_MalformedCMapNoiseFilter())

_DOCX_PART_RE = re.compile(r"word/(?:document|header\d+|footer\d+)\.xml$")
_OUTGOING_RE = re.compile(
    r"№\s*(?P<number>\d{1,8})\s*[-–—]?\s*КП\s+от\s+(?P<date>\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)
_WORK_SCOPE_RE = re.compile(
    r"MUN_NAME_2\s+MUN_R_NAME(?:_1)?\s+SUB_RF(?:_1)?",
    re.IGNORECASE,
)
_DISTRICT_SCOPE_RE = re.compile(
    r"MUN_R_NAME(?:_1)?\s+SUB_RF(?:_1)?",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(r"Уважаем(?:ый|ая)\s*\(ая\)\s+HEAD_FIO\s*!", re.IGNORECASE)
_TOKEN_RE = re.compile(r"(?<![A-Z0-9_])([A-Z][A-Z0-9_]{2,})(?![A-Z0-9_])")

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

_FONT_GLYPHS = (
    "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ "
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя "
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz "
    "0123456789 №.,:;!?()—–-«»₽/@+"
)


@dataclass(frozen=True)
class AutoCompileResult:
    source_path: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class _TextReplacement:
    start: int
    end: int
    replacement: str
    fields: tuple[str, ...]
    strategy: str
    confidence: float


@dataclass(frozen=True)
class _PdfRegion:
    page_index: int
    field_name: str
    source_box: tuple[float, float, float, float]
    field_box: tuple[float, float, float, float]
    multiline: bool
    strategy: str
    confidence: float
    redaction_boxes: tuple[tuple[float, float, float, float], ...] = ()


def build_reference_context(data_path: Path | None, *, work_type: str | None = None) -> dict[str, Any]:
    if data_path is None or not Path(data_path).is_file():
        return {}
    try:
        from src.generator.generation.excel_io import load_rows
        from src.generator.generation.transforms import build_document_context
        from src.generator.generation.config_generator import START_OUTGOING_NUMBER

        workbook, _, rows = load_rows(Path(data_path))
        close = getattr(workbook, "close", None)
        if callable(close):
            close()
        if not rows:
            return {}
        return build_document_context(rows[0], START_OUTGOING_NUMBER, work_type=work_type)
    except Exception:
        return {}


def _paragraph_text_nodes(root: etree._Element) -> list[list[etree._Element]]:
    result: list[list[etree._Element]] = []
    for paragraph in root.xpath("//*[local-name()='p']"):
        nodes = list(paragraph.xpath(".//*[local-name()='t']"))
        if nodes:
            result.append(nodes)
    return result


def _replacement_candidates(text: str, reference_context: dict[str, Any]) -> list[_TextReplacement]:
    candidates: list[_TextReplacement] = []
    for match in _OUTGOING_RE.finditer(text):
        candidates.append(
            _TextReplacement(
                match.start(),
                match.end(),
                "№ {{OUTGOING_NUMBER}}-КП от {{DATE}}",
                ("OUTGOING_NUMBER", "DATE"),
                "semantic_outgoing_line",
                1.0,
            )
        )
    for pattern, field_name in (
        (_WORK_SCOPE_RE, "WORK_SCOPE_FRAGMENT"),
        (_DISTRICT_SCOPE_RE, "MUN_R_SCOPE_FRAGMENT"),
    ):
        for match in pattern.finditer(text):
            candidates.append(
                _TextReplacement(
                    match.start(),
                    match.end(),
                    f"{{{{{field_name}}}}}",
                    (field_name,),
                    "legacy_scope_marker",
                    1.0,
                )
            )
    for match in _GREETING_RE.finditer(text):
        candidates.append(
            _TextReplacement(
                match.start(),
                match.end(),
                "{{HEAD_GREETING}}",
                ("HEAD_GREETING",),
                "legacy_greeting_marker",
                1.0,
            )
        )
    for match in _TOKEN_RE.finditer(text):
        field_name = _TOKEN_FIELDS.get(match.group(1).upper())
        if field_name:
            candidates.append(
                _TextReplacement(
                    match.start(),
                    match.end(),
                    f"{{{{{field_name}}}}}",
                    (field_name,),
                    "legacy_symbolic_marker",
                    0.99,
                )
            )
    for field_name in _REFERENCE_FIELDS:
        raw_value = str(reference_context.get(field_name) or "").strip()
        if len(raw_value) < 4:
            continue
        for match in re.finditer(re.escape(raw_value), text, flags=re.IGNORECASE):
            candidates.append(
                _TextReplacement(
                    match.start(),
                    match.end(),
                    f"{{{{{field_name}}}}}",
                    (field_name,),
                    "reference_value_match",
                    0.94,
                )
            )

    selected: list[_TextReplacement] = []
    occupied: list[tuple[int, int]] = []
    for item in sorted(
        candidates,
        key=lambda value: (-value.confidence, -(value.end - value.start), value.start),
    ):
        if any(item.start < end and item.end > start for start, end in occupied):
            continue
        occupied.append((item.start, item.end))
        selected.append(item)
    return sorted(selected, key=lambda value: value.start)


def _replace_range(nodes: list[etree._Element], start: int, end: int, replacement: str) -> None:
    texts = [node.text or "" for node in nodes]
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for value in texts:
        offsets.append((cursor, cursor + len(value)))
        cursor += len(value)
    start_index = next(index for index, (_, right) in enumerate(offsets) if start < right)
    end_index = next(index for index, (left, right) in enumerate(offsets) if end <= right and end > left)
    start_left = offsets[start_index][0]
    end_left = offsets[end_index][0]
    if start_index == end_index:
        value = nodes[start_index].text or ""
        nodes[start_index].text = value[: start - start_left] + replacement + value[end - end_left :]
        return
    start_text = nodes[start_index].text or ""
    end_text = nodes[end_index].text or ""
    nodes[start_index].text = start_text[: start - start_left] + replacement
    for index in range(start_index + 1, end_index):
        nodes[index].text = ""
    nodes[end_index].text = end_text[end - end_left :]


def _compile_docx(source_path: Path, output_path: Path, reference_context: dict[str, Any]) -> AutoCompileResult:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
    field_counts: dict[str, int] = {}
    strategies: dict[str, int] = {}
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(output_path, "w") as target:
        part_names = {name for name in source.namelist() if _DOCX_PART_RE.fullmatch(name)}
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename in part_names:
                root = etree.fromstring(payload, parser)
                for nodes in _paragraph_text_nodes(root):
                    text = "".join(node.text or "" for node in nodes)
                    replacements = _replacement_candidates(text, reference_context)
                    for item in reversed(replacements):
                        _replace_range(nodes, item.start, item.end, item.replacement)
                    for item in replacements:
                        strategies[item.strategy] = strategies.get(item.strategy, 0) + 1
                        for field_name in item.fields:
                            field_counts[field_name] = field_counts.get(field_name, 0) + 1
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            target.writestr(info, payload)
    if not field_counts:
        output_path.unlink(missing_ok=True)
        raise TemplateCompileError(
            "Сервис не смог безопасно определить изменяемые зоны DOCX. "
            "Загрузите таблицу с данными до шаблона или используйте документ с примером получателя."
        )
    return AutoCompileResult(
        output_path,
        {
            "mode": "automatic",
            "format": "docx",
            "fields": field_counts,
            "strategies": strategies,
            "reference_context_used": bool(reference_context),
        },
    )


def _fitz_module():
    try:
        import fitz
    except ImportError as exc:
        raise TemplateCompileError("Авторазметка PDF требует PyMuPDF в app/worker image") from exc
    return fitz


def _font_path() -> Path:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise TemplateCompileError("Не найден Unicode-шрифт для автоматически размеченного PDF")


def _rect_tuple(rect: Any) -> tuple[float, float, float, float]:
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _overlap_ratio(first: Any, second: Any) -> float:
    intersection = first & second
    if intersection.is_empty:
        return 0.0
    smaller = min(max(first.get_area(), 0.01), max(second.get_area(), 0.01))
    return float(intersection.get_area()) / smaller


def _table_cell_right_boundary(page: Any, box: Any) -> float | None:
    fitz = _fitz_module()
    source = fitz.Rect(box)
    candidates: list[float] = []
    try:
        for drawing in page.get_drawings():
            rect = fitz.Rect(drawing.get("rect"))
            is_vertical_rule = rect.width <= 1.5 and rect.height >= max(12.0, source.height * 1.1)
            spans_field = rect.y0 <= source.y0 + 2 and rect.y1 >= source.y1 - 2
            if is_vertical_rule and spans_field and rect.x0 > source.x0 + 2:
                candidates.append(float(rect.x0))
    except Exception:
        return None
    return min(candidates) if candidates else None


def _field_box(page: Any, source_box: Any, field_name: str) -> Any:
    fitz = _fitz_module()
    box = fitz.Rect(source_box)
    margin_right = max(box.x1, page.rect.width - 34)
    if field_name == "OUTGOING_NUMBER":
        return fitz.Rect(box.x0 - 1, box.y0 - 1, box.x1 - 0.25, box.y1 + 1)
    if field_name in {"ADM_NAME", "ADM_NAME_1"}:
        bottom = box.y1 + 2 if box.height > 20 else box.y0 + box.height * 2.7
        return fitz.Rect(box.x0, box.y0 - 1, margin_right, min(page.rect.height - 18, bottom))
    if field_name in {"WORK_SCOPE_FRAGMENT", "MUN_R_SCOPE_FRAGMENT", "WORK_TITLE", "WORK_TITLE_1"}:
        table_right = _table_cell_right_boundary(page, box)
        if table_right is not None:
            return fitz.Rect(box.x0, box.y0 - 1, table_right - 2, box.y1 + 1)
        center = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        containing: list[Any] = []
        try:
            for drawing in page.get_drawings():
                rect = fitz.Rect(drawing.get("rect"))
                if rect.contains(center) and rect.width > box.width * 1.15 and rect.height > box.height * 1.15:
                    containing.append(rect)
        except Exception:
            containing = []
        if containing:
            cell = min(containing, key=lambda value: value.get_area())
            return fitz.Rect(max(box.x0, cell.x0 + 2), cell.y0 + 2, cell.x1 - 2, cell.y1 - 2)
        bottom = box.y1 + 2 if box.height > 20 else box.y0 + box.height * 2.5
        return fitz.Rect(box.x0, box.y0 - 1, margin_right, min(page.rect.height - 18, bottom))
    return fitz.Rect(box.x0 - 1, box.y0 - 1, box.x1 + 2, box.y1 + 1)

def _add_pdf_region(
    regions: list[_PdfRegion],
    page: Any,
    page_index: int,
    field_name: str,
    source_box: Any,
    *,
    strategy: str,
    confidence: float,
    redaction_boxes: list[Any] | None = None,
    target_box: Any | None = None,
) -> None:
    fitz = _fitz_module()
    source = fitz.Rect(source_box)
    if source.is_empty or source.width < 1 or source.height < 1:
        return
    if any(item.page_index == page_index and _overlap_ratio(source, fitz.Rect(item.source_box)) > 0.72 for item in regions):
        return
    target = fitz.Rect(target_box) if target_box is not None else _field_box(page, source, field_name)
    regions.append(
        _PdfRegion(
            page_index=page_index,
            field_name=field_name,
            source_box=_rect_tuple(source),
            field_box=_rect_tuple(target),
            multiline=field_name in {"ADM_NAME", "ADM_NAME_1", "WORK_SCOPE_FRAGMENT", "MUN_R_SCOPE_FRAGMENT", "WORK_TITLE", "WORK_TITLE_1"},
            strategy=strategy,
            confidence=confidence,
            redaction_boxes=tuple(_rect_tuple(fitz.Rect(box)) for box in (redaction_boxes or [source])),
        )
    )


def _nearest_pair(first_boxes: list[Any], second_boxes: list[Any]) -> list[tuple[Any, Any]]:
    pairs: list[tuple[Any, Any]] = []
    remaining = list(second_boxes)
    for first in first_boxes:
        candidates = [
            second
            for second in remaining
            if abs(second.y0 - first.y0) <= max(first.height, second.height) * 1.6 and second.x0 >= first.x0
        ]
        if not candidates:
            continue
        second = min(candidates, key=lambda value: abs(value.y0 - first.y0) + max(0.0, value.x0 - first.x1))
        remaining.remove(second)
        pairs.append((first, second))
    return pairs


def _merge_search_boxes(boxes: list[Any]) -> list[Any]:
    fitz = _fitz_module()
    ordered = sorted((fitz.Rect(box) for box in boxes), key=lambda value: (round(value.y0, 1), value.x0))
    groups: list[list[Any]] = []
    for box in ordered:
        if not groups:
            groups.append([box])
            continue
        previous = groups[-1][-1]
        same_line = abs(box.y0 - previous.y0) <= max(box.height, previous.height) * 0.35
        next_line = 0 <= box.y0 - previous.y1 <= max(box.height, previous.height) * 0.75
        if same_line or next_line:
            groups[-1].append(box)
        else:
            groups.append([box])
    merged: list[Any] = []
    for group in groups:
        result = fitz.Rect(group[0])
        for box in group[1:]:
            result |= box
        merged.append(result)
    return merged

def _normalized_word(value: str) -> str:
    return re.sub(r"(^\W+|\W+$)", "", str(value).casefold(), flags=re.UNICODE)


def _search_value_box_groups(page: Any, value: str) -> list[list[Any]]:
    fitz = _fitz_module()
    target_words = [item for item in (_normalized_word(part) for part in value.split()) if item]
    if len(target_words) <= 1:
        return [[box] for box in page.search_for(value)]
    words = sorted(page.get_text("words"), key=lambda item: (int(item[5]), int(item[6]), int(item[7])))
    normalized = [_normalized_word(str(item[4])) for item in words]
    matches: list[list[Any]] = []
    index = 0
    while index <= len(words) - len(target_words):
        if normalized[index : index + len(target_words)] != target_words:
            index += 1
            continue
        matched_words = words[index : index + len(target_words)]
        line_boxes: list[Any] = []
        current_key: tuple[int, int] | None = None
        for word in matched_words:
            key = (int(word[5]), int(word[6]))
            word_box = fitz.Rect(word[:4])
            if key == current_key:
                line_boxes[-1] |= word_box
            else:
                line_boxes.append(word_box)
                current_key = key
        matches.append(line_boxes)
        index += len(target_words)
    if matches:
        return matches
    return [[box] for box in page.search_for(value)]


def _bounding_box(boxes: list[Any]) -> Any:
    fitz = _fitz_module()
    result = fitz.Rect(boxes[0])
    for box in boxes[1:]:
        result |= fitz.Rect(box)
    return result


def _search_value_boxes(page: Any, value: str) -> list[Any]:
    return [_bounding_box(group) for group in _search_value_box_groups(page, value)]

def _find_pdf_regions(document: Any, reference_context: dict[str, Any]) -> list[_PdfRegion]:
    fitz = _fitz_module()
    regions: list[_PdfRegion] = []
    for page_index, page in enumerate(document):
        mun_boxes = list(page.search_for("MUN_R_NAME"))
        sub_boxes = list(page.search_for("SUB_RF"))
        for first, second in _nearest_pair(mun_boxes, sub_boxes):
            _add_pdf_region(
                regions,
                page,
                page_index,
                "MUN_R_SCOPE_FRAGMENT",
                first | second,
                strategy="legacy_scope_marker",
                confidence=1.0,
            )

        for token, field_name in _TOKEN_FIELDS.items():
            if token in {"MUN_R_NAME", "SUB_RF", "MUN_R_NAME_1", "SUB_RF_1"}:
                continue
            for box in page.search_for(token):
                _add_pdf_region(
                    regions,
                    page,
                    page_index,
                    field_name,
                    box,
                    strategy="legacy_symbolic_marker",
                    confidence=0.99,
                )

        page_text = page.get_text("text")
        for match in _OUTGOING_RE.finditer(page_text):
            number_boxes = list(page.search_for(match.group("number")))
            date_boxes = list(page.search_for(match.group("date")))
            pairs = [
                (number_box, date_box)
                for number_box in number_boxes
                for date_box in date_boxes
                if number_box.x0 < date_box.x0
                and abs(number_box.y0 - date_box.y0) <= max(number_box.height, date_box.height) * 0.75
            ]
            if not pairs:
                continue
            number_box, date_box = min(
                pairs,
                key=lambda pair: abs(pair[1].x0 - pair[0].x1) + abs(pair[1].y0 - pair[0].y0),
            )
            for field_name, box in (("OUTGOING_NUMBER", number_box), ("DATE", date_box)):
                _add_pdf_region(
                    regions,
                    page,
                    page_index,
                    field_name,
                    box,
                    strategy="semantic_outgoing_line",
                    confidence=1.0,
                )
        mun_value = str(reference_context.get("MUN_R_NAME_1") or "").strip()
        sub_value = str(reference_context.get("SUB_RF_1") or "").strip()
        if mun_value and sub_value:
            mun_groups = _search_value_box_groups(page, mun_value)
            sub_groups = _search_value_box_groups(page, sub_value)
            remaining_sub_groups = list(sub_groups)
            for mun_group in mun_groups:
                mun_box = _bounding_box(mun_group)
                candidates = [
                    group
                    for group in remaining_sub_groups
                    if abs(_bounding_box(group).y0 - mun_box.y0) <= max(42.0, mun_box.height * 3.5)
                ]
                if not candidates:
                    continue
                sub_group = min(candidates, key=lambda group: abs(_bounding_box(group).y0 - mun_box.y0))
                remaining_sub_groups.remove(sub_group)
                sub_box = _bounding_box(sub_group)
                source_parts = [*mun_group, *sub_group]
                source_box = _bounding_box(source_parts)
                target_seed = fitz.Rect(
                    mun_box.x0,
                    min(mun_box.y0, sub_box.y0) - 1,
                    max(mun_box.x1, sub_box.x1),
                    max(mun_box.y1, sub_box.y1) + 2,
                )
                target_box = _field_box(page, target_seed, "MUN_R_SCOPE_FRAGMENT")
                if target_seed.height > 18 and target_box.height > target_seed.height * 1.7:
                    target_box.y1 = target_seed.y1
                _add_pdf_region(
                    regions,
                    page,
                    page_index,
                    "MUN_R_SCOPE_FRAGMENT",
                    source_box,
                    strategy="reference_scope_pair",
                    confidence=0.97,
                    redaction_boxes=source_parts,
                    target_box=target_box,
                )
        reference_values = sorted(
            (
                (field_name, str(reference_context.get(field_name) or "").strip())
                for field_name in _REFERENCE_FIELDS if field_name not in {"WORK_TITLE", "WORK_TITLE_1", "MUN_R_NAME", "MUN_R_NAME_1", "SUB_RF", "SUB_RF_1"}
            ),
            key=lambda item: len(item[1]),
            reverse=True,
        )
        for field_name, value in reference_values:
            if len(value) < 4:
                continue
            for box in _search_value_boxes(page, value):
                _add_pdf_region(
                    regions,
                    page,
                    page_index,
                    field_name,
                    box,
                    strategy="reference_value_match",
                    confidence=0.94,
                )
    return regions


def _pdf_box(rect: tuple[float, float, float, float], page_height: float) -> ArrayObject:
    x0, y0, x1, y1 = rect
    return ArrayObject(
        [
            FloatObject(x0),
            FloatObject(page_height - y1),
            FloatObject(x1),
            FloatObject(page_height - y0),
        ]
    )


def _expanded_redaction_box(page: Any, box: Any, field_name: str) -> Any:
    fitz = _fitz_module()
    rect = fitz.Rect(box)
    right_margin = 0 if field_name == "OUTGOING_NUMBER" else 1 if field_name == "DATE" else 10
    return fitz.Rect(rect.x0 - 1, rect.y0 - 1, rect.x1 + right_margin, rect.y1 + 1) & page.rect


def _sample_background_color(page: Any, box: Any) -> tuple[float, float, float]:
    fitz = _fitz_module()
    rect = fitz.Rect(box)
    clip = fitz.Rect(rect.x0 - 3, rect.y0 - 3, rect.x1 + 3, rect.y1 + 3) & page.rect
    try:
        pixmap = page.get_pixmap(clip=clip, matrix=fitz.Matrix(1, 1), alpha=False)
        if pixmap.width < 2 or pixmap.height < 2 or pixmap.n < 3:
            return (1.0, 1.0, 1.0)
        samples = pixmap.samples
        colors: dict[tuple[int, int, int], int] = {}
        for y in range(pixmap.height):
            for x in range(pixmap.width):
                if not ((x < 3 or x >= pixmap.width - 3) and (y < 3 or y >= pixmap.height - 3)):
                    continue
                offset = (y * pixmap.width + x) * pixmap.n
                raw = samples[offset : offset + 3]
                color = tuple(min(255, int(round(int(channel) / 16.0)) * 16) for channel in raw)
                colors[color] = colors.get(color, 0) + 1
        red, green, blue = max(colors, key=lambda color: (colors[color], sum(color)))
        return (red / 255.0, green / 255.0, blue / 255.0)
    except Exception:
        return (1.0, 1.0, 1.0)

_VALIDITY_TEXT_RE = re.compile(
    r"Срок\s+действия\s+коммерческого\s+предложения.*?\d{2}\.\d{2}\.\d{4}",
    re.IGNORECASE | re.DOTALL,
)


def _validity_line_boxes(page: Any) -> list[Any]:
    fitz = _fitz_module()
    boxes: list[Any] = []
    for block in page.get_text("blocks"):
        text = str(block[4] or "")
        if _VALIDITY_TEXT_RE.search(text):
            boxes.append(fitz.Rect(block[:4]))
    return boxes


def _relocate_images_over_validity(document: Any, page: Any, page_index: int) -> list[dict[str, Any]]:
    fitz = _fitz_module()
    validity_boxes = _validity_line_boxes(page)
    if not validity_boxes:
        return []
    repairs: list[dict[str, Any]] = []
    processed_xrefs: set[int] = set()
    for image in page.get_image_info(xrefs=True):
        xref = int(image.get("xref") or 0)
        if xref <= 0 or xref in processed_xrefs:
            continue
        image_box = fitz.Rect(image.get("bbox"))
        if image_box.get_area() < page.rect.get_area() * 0.015:
            continue
        overlapping = [box for box in validity_boxes if not (image_box & box).is_empty]
        if not overlapping:
            continue
        required_top = max(box.y1 for box in overlapping) + 3
        shift = required_top - image_box.y0
        if shift <= 0 or image_box.y1 + shift > page.rect.height - 8:
            continue
        extracted = document.extract_image(xref)
        image_bytes = extracted.get("image")
        if not image_bytes:
            continue
        moved_box = fitz.Rect(image_box.x0, image_box.y0 + shift, image_box.x1, image_box.y1 + shift)
        page.delete_image(xref)
        page.insert_image(moved_box, stream=image_bytes, overlay=True)
        processed_xrefs.add(xref)
        repairs.append(
            {
                "type": "move_image_below_validity_date",
                "page": page_index + 1,
                "from": list(_rect_tuple(image_box)),
                "to": list(_rect_tuple(moved_box)),
            }
        )
    return repairs

def _compile_pdf(source_path: Path, output_path: Path, reference_context: dict[str, Any]) -> AutoCompileResult:
    fitz = _fitz_module()
    font_path = _font_path()
    intermediate = output_path.with_name(f".{output_path.stem}.background.pdf")
    with fitz.open(source_path) as document:
        regions = _find_pdf_regions(document, reference_context)
        if not regions:
            raise TemplateCompileError(
                "Сервис не смог безопасно определить изменяемые зоны PDF. "
                "Загрузите таблицу с данными до шаблона или PDF с примером получателя."
            )
        layout_repairs: list[dict[str, Any]] = []
        for page_index, page in enumerate(document):
            layout_repairs.extend(_relocate_images_over_validity(document, page, page_index))
            page_regions = [item for item in regions if item.page_index == page_index]
            for item in page_regions:
                for redaction_box in item.redaction_boxes or (item.source_box,):
                    expanded_box = _expanded_redaction_box(page, redaction_box, item.field_name)
                    page.add_redact_annot(
                        expanded_box,
                        fill=_sample_background_color(page, expanded_box),
                        cross_out=False,
                    )
            if page_regions:
                page.apply_redactions(images=0, graphics=0, text=0)
            page.insert_text(
                (1, max(2, page.rect.height - 2)),
                _FONT_GLYPHS,
                fontsize=1,
                fontname="AutoTemplateFont",
                fontfile=str(font_path),
                render_mode=3,
                overlay=True,
            )
        document.save(intermediate, garbage=4, deflate=True)

    try:
        reader = PdfReader(str(intermediate))
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        first_page = writer.pages[0]
        resources = first_page[NameObject("/Resources")].get_object()
        font_resources = resources.get(NameObject("/Font"))
        fonts = font_resources.get_object() if font_resources is not None else DictionaryObject()
        if not fonts:
            raise TemplateCompileError("В PDF не удалось встроить Unicode-шрифт для полей")

        def font_score(item: tuple[Any, Any]) -> tuple[int, int, int, int]:
            key, reference = item
            font = reference.get_object()
            base_name = str(font.get("/BaseFont") or "")
            return (
                1 if "AutoTemplateFont" in str(key) else 0,
                1 if font.get("/Subtype") == "/Type0" else 0,
                1 if font.get("/ToUnicode") is not None else 0,
                1 if "Bold" not in base_name else 0,
            )

        font_key, font_ref = max(fonts.items(), key=font_score)
        fields = ArrayObject()
        acro_form = DictionaryObject(
            {
                NameObject("/Fields"): fields,
                NameObject("/NeedAppearances"): BooleanObject(False),
                NameObject("/DA"): TextStringObject(f"{font_key} 0 Tf 0 g"),
                NameObject("/DR"): DictionaryObject(
                    {NameObject("/Font"): DictionaryObject({NameObject(str(font_key)): font_ref})}
                ),
            }
        )
        writer._root_object[NameObject("/AcroForm")] = writer._add_object(acro_form)
        instance_counts: dict[str, int] = {}
        for item in regions:
            instance_counts[item.field_name] = instance_counts.get(item.field_name, 0) + 1
            instance_number = instance_counts[item.field_name]
            raw_field_name = item.field_name if instance_number == 1 else f"{item.field_name}__{instance_number}"
            page = writer.pages[item.page_index]
            annotations = page.get(NameObject("/Annots"), ArrayObject())
            annotations = annotations.get_object() if hasattr(annotations, "get_object") else annotations
            page[NameObject("/Annots")] = annotations
            page_height = float(page.mediabox.height)
            widget = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Annot"),
                    NameObject("/Subtype"): NameObject("/Widget"),
                    NameObject("/FT"): NameObject("/Tx"),
                    NameObject("/T"): TextStringObject(raw_field_name),
                    NameObject("/V"): TextStringObject(""),
                    NameObject("/Rect"): _pdf_box(item.field_box, page_height),
                    NameObject("/DA"): TextStringObject(f"{font_key} 0 Tf 0 g"),
                    NameObject("/F"): NumberObject(4),
                    NameObject("/Ff"): NumberObject(4096 if item.multiline else 0),
                    NameObject("/Q"): NumberObject(0),
                    NameObject("/P"): page.indirect_reference,
                    NameObject("/BS"): DictionaryObject(
                        {NameObject("/W"): NumberObject(0), NameObject("/S"): NameObject("/S")}
                    ),
                }
            )
            widget_ref = writer._add_object(widget)
            fields.append(widget_ref)
            annotations.append(widget_ref)
        with output_path.open("wb") as handle:
            writer.write(handle)
    finally:
        intermediate.unlink(missing_ok=True)

    field_counts: dict[str, int] = {}
    strategies: dict[str, int] = {}
    for item in regions:
        field_counts[item.field_name] = field_counts.get(item.field_name, 0) + 1
        strategies[item.strategy] = strategies.get(item.strategy, 0) + 1
    return AutoCompileResult(
        output_path,
        {
            "mode": "automatic",
            "format": "pdf",
            "fields": field_counts,
            "strategies": strategies,
            "reference_context_used": bool(reference_context),
            "layout_repairs": layout_repairs,
            "regions": [
                {
                    "field_name": item.field_name,
                    "page": item.page_index + 1,
                    "box": list(item.field_box),
                }
                for item in regions
            ],
        },
    )



def _compile_pdf_semantic(
    source_path: Path,
    output_path: Path,
    reference_context: dict[str, Any],
) -> AutoCompileResult:
    """Compile an ordinary PDF to immutable artwork plus adaptive text layers."""

    from .pdf_overlay_compiler import build_pdf_overlay_html

    html, overlay_report = build_pdf_overlay_html(source_path, reference_context)
    output_path.write_text(html, encoding="utf-8")
    return AutoCompileResult(
        output_path,
        {
            "mode": "automatic",
            "format": "pdf",
            "compiled_format": "html",
            "fields": overlay_report["fields"],
            "strategies": overlay_report["strategies"],
            "reference_context_used": bool(reference_context),
            "visual_overlay": overlay_report,
        },
    )

def auto_compile_template(
    source_path: Path,
    output_dir: Path,
    *,
    reference_context: dict[str, Any] | None = None,
) -> AutoCompileResult:
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix.lower()
    context = dict(reference_context or {})
    if suffix == ".docx":
        output_path = output_dir / "normalized.docx"
        return _compile_docx(source_path, output_path, context)
    if suffix == ".pdf":
        output_path = output_dir / "normalized.html"
        return _compile_pdf_semantic(source_path, output_path, context)
    if suffix in {".html", ".htm"}:
        output_path = output_dir / f"normalized{suffix}"
        shutil.copy2(source_path, output_path)
        return AutoCompileResult(output_path, {"mode": "passthrough", "format": suffix.lstrip(".")})
    raise TemplateCompileError(f"Автоматическая компиляция формата {suffix or 'unknown'} не поддерживается")
