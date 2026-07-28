from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from src.campaigns.pdf_overlay_service import (
    FONT_FILES,
    PDF_GEOMETRY_TOLERANCE,
    PDF_PAGE_MARGIN,
    PdfOverlayLayoutError,
    analyze_pdf,
    render_pdf,
    render_pdf_with_discovered_placeholders,
)
from src.campaigns.substitution_engine import discover_placeholders


LONG_ADMIN_NAME = (
    'Администрация муниципального образования "Энемское городское поселение"'
)


def _highlighted_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(100, 100, 172, 118), color=None, fill=(1, 1, 0), overlay=True)
    page.insert_text(fitz.Point(101, 114), "ADM_NAME", fontsize=11)
    data = document.tobytes()
    document.close()
    return data


def test_detects_and_edits_highlighted_pdf_field_without_changing_source() -> None:
    source = _highlighted_pdf()
    source_before = bytes(source)
    state = analyze_pdf(source)

    assert state["page_count"] == 1
    assert len(state["fields"]) == 1
    assert state["fields"][0]["variable"] == "ADM_NAME"
    assert state["fields"][0]["source_text"] == "ADM_NAME"

    state["fields"][0]["value"] = "Ivanov I.I."
    edited = render_pdf(source, state)

    assert edited != source
    assert source == source_before
    document = fitz.open(stream=edited, filetype="pdf")
    try:
        assert "Ivanov I.I." in document[0].get_text().replace("\u00a0", " ")
        assert "ADM_NAME" not in document[0].get_text()
    finally:
        document.close()


def _unicode_font_file() -> str:
    font_file = next((path for path in FONT_FILES if Path(path).exists()), None)
    if font_file is None:
        pytest.skip("A Unicode TTF font is required for the PDF overlay regression fixture.")
    return font_file


def _placeholder_pdf() -> bytes:
    font_file = _unicode_font_file()
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    font_kwargs = {"fontname": "RegressionSans", "fontfile": font_file}
    page.insert_text(
        fitz.Point(65, 100),
        "№ {{id}}-ИП от {{current_date}}",
        fontsize=11,
        **font_kwargs,
    )
    page.insert_text(fitz.Point(360, 100), "ADM_NAME", fontsize=11, **font_kwargs)
    page.insert_text(fitz.Point(360, 165), "Следующий блок", fontsize=11, **font_kwargs)
    data = document.tobytes()
    document.close()
    return data


def _rendered_placeholder_pdf() -> bytes:
    source = _placeholder_pdf()
    document = fitz.open(stream=source, filetype="pdf")
    try:
        source_text = document[0].get_text("text")
    finally:
        document.close()
    return render_pdf_with_discovered_placeholders(
        source,
        discover_placeholders(source_text),
        {
            "id": "13",
            "current_date": "28.07.2026",
            "ADM_NAME": LONG_ADMIN_NAME,
        },
    )


def test_discovered_brace_date_is_rendered_once() -> None:
    document = fitz.open(stream=_rendered_placeholder_pdf(), filetype="pdf")
    try:
        spans = [
            span["text"]
            for block in document[0].get_text("dict").get("blocks", [])
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ]
        assert spans.count("28.07.2026") == 1
    finally:
        document.close()


def test_redaction_preserves_hyphen_next_to_identifier() -> None:
    document = fitz.open(stream=_rendered_placeholder_pdf(), filetype="pdf")
    try:
        rendered_text = document[0].get_text("text")
        assert "-" in rendered_text or "\u00ad" in rendered_text
    finally:
        document.close()


def test_long_administration_name_wraps_before_right_page_margin() -> None:
    document = fitz.open(stream=_rendered_placeholder_pdf(), filetype="pdf")
    try:
        page = document[0]
        line_rects = page.search_for(LONG_ADMIN_NAME)
        assert len(line_rects) >= 2
        assert max(rect.x1 for rect in line_rects) <= page.rect.x1 - PDF_PAGE_MARGIN + 1
    finally:
        document.close()


def test_rendered_text_stays_inside_page_bounds() -> None:
    document = fitz.open(stream=_rendered_placeholder_pdf(), filetype="pdf")
    try:
        page = document[0]
        page_bounds = fitz.Rect(
            page.rect.x0 - PDF_GEOMETRY_TOLERANCE,
            page.rect.y0 - PDF_GEOMETRY_TOLERANCE,
            page.rect.x1 + PDF_GEOMETRY_TOLERANCE,
            page.rect.y1 + PDF_GEOMETRY_TOLERANCE,
        )
        for block in page.get_text("rawdict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    for char in span.get("chars", []):
                        assert page_bounds.contains(fitz.Rect(char["bbox"]))
    finally:
        document.close()


def test_render_pdf_rejects_overlapping_inserted_blocks() -> None:
    source = _highlighted_pdf()
    state = {
        "fields": [
            {
                "id": "one",
                "page": 0,
                "source_text": "one",
                "value": "First",
                "x": 100,
                "y": 100,
                "width": 100,
                "height": 30,
                "font_size": 10,
                "background": "#ffffff",
            },
            {
                "id": "two",
                "page": 0,
                "source_text": "two",
                "value": "Second",
                "x": 150,
                "y": 100,
                "width": 100,
                "height": 30,
                "font_size": 10,
                "background": "#ffffff",
            },
        ]
    }
    with pytest.raises(PdfOverlayLayoutError, match="перекрываются"):
        render_pdf(source, state)
