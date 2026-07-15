from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_VALIDITY_TEXT_RE = re.compile(
    r"Срок\s+действия\s+коммерческого\s+предложения.*?\d{2}\.\d{2}\.\d{4}",
    re.IGNORECASE | re.DOTALL,
)


def _union_char_boxes(textpage: Any, start: int, end: int) -> tuple[float, float, float, float] | None:
    text = textpage.get_text_range()
    boxes: list[tuple[float, float, float, float]] = []
    for index in range(start, min(end, textpage.count_chars())):
        if text[index].isspace():
            continue
        try:
            box = tuple(float(value) for value in textpage.get_charbox(index, loose=True))
        except Exception:
            continue
        if box[2] > box[0] and box[3] > box[1]:
            boxes.append(box)
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _intersects(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    return min(first[2], second[2]) > max(first[0], second[0]) and min(first[3], second[3]) > max(first[1], second[1])


def pdf_layout_issues(pdf_path: Path) -> list[dict[str, Any]]:
    try:
        import pypdfium2 as pdfium
        from pypdfium2 import raw
    except ImportError:
        return [{"type": "layout_check_unavailable", "message": "PDFium is unavailable"}]
    issues: list[dict[str, Any]] = []
    try:
        document = pdfium.PdfDocument(pdf_path)
        for page_index, page in enumerate(document, start=1):
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            validity_boxes = [
                box
                for match in _VALIDITY_TEXT_RE.finditer(text)
                if (box := _union_char_boxes(textpage, match.start(), match.end())) is not None
            ]
            if not validity_boxes:
                continue
            width, height = page.get_size()
            for image in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE]):
                image_box = tuple(float(value) for value in image.get_bounds())
                image_area = max(0.0, image_box[2] - image_box[0]) * max(0.0, image_box[3] - image_box[1])
                if image_area < width * height * 0.015:
                    continue
                for text_box in validity_boxes:
                    if _intersects(image_box, text_box):
                        issues.append(
                            {
                                "type": "image_overlaps_validity_date",
                                "page": page_index,
                                "text_box": list(text_box),
                                "image_box": list(image_box),
                            }
                        )
    except Exception as exc:
        return [{"type": "layout_check_failed", "message": f"{type(exc).__name__}: {exc}"}]
    return issues
