from __future__ import annotations

import fitz

from src.campaigns.pdf_overlay_service import analyze_pdf, render_pdf


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
    state = analyze_pdf(source)

    assert state["page_count"] == 1
    assert len(state["fields"]) == 1
    assert state["fields"][0]["variable"] == "ADM_NAME"
    assert state["fields"][0]["source_text"] == "ADM_NAME"

    state["fields"][0]["value"] = "Ivanov I.I."
    edited = render_pdf(source, state)

    assert edited != source
    assert source == _highlighted_pdf()
    document = fitz.open(stream=edited, filetype="pdf")
    try:
        assert "Ivanov I.I." in document[0].get_text()
        assert "ADM_NAME" not in document[0].get_text()
    finally:
        document.close()