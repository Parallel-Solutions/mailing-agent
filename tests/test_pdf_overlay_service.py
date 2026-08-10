from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from src.campaigns import document_layout_service, pdf_overlay_service
from src.campaigns.pdf_overlay_service import (
    FONT_FILES,
    PDF_GEOMETRY_TOLERANCE,
    PDF_AUTO_LAYOUT_VERSION,
    PDF_PAGE_MARGIN,
    PdfOverlayLayoutError,
    analyze_pdf,
    build_auto_layout_state,
    render_pdf,
    render_pdf_with_discovered_placeholders,
    resolve_layout_field_value,
)
from src.campaigns.template_render_service import _render_pdf_overlay
from src.campaigns.substitution_engine import discover_placeholders


LONG_ADMIN_NAME = (
    'Администрация муниципального образования "Энемское городское поселение"'
)
LONG_ADMIN_NAME_DATIVE = (
    'Администрации муниципального образования "Энемское городское поселение"'
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


def _placeholder_pdf(*, font_size: float = 11) -> bytes:
    font_file = _unicode_font_file()
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    font_kwargs = {"fontname": "RegressionSans", "fontfile": font_file}
    page.insert_text(
        fitz.Point(65, 100),
        "№ {{id}}-ИП от {{current_date}}",
        fontsize=font_size,
        **font_kwargs,
    )
    page.insert_text(fitz.Point(360, 100), "ADM_NAME", fontsize=font_size, **font_kwargs)
    page.insert_text(fitz.Point(360, 165), "Следующий блок", fontsize=11, **font_kwargs)
    page.insert_text(
        fitz.Point(210, 205),
        "Уважаемый {{Имя Отчество}}!",
        fontsize=11,
        **font_kwargs,
    )
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
        corporate_layout=False,
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


def test_auto_layout_groups_related_fields_and_preserves_readable_font_size() -> None:
    source = _placeholder_pdf()
    context = {
        "id": "125",
        "current_date": "28.07.2026",
        "ADM_NAME": LONG_ADMIN_NAME,
        "ADM_NAME_1": LONG_ADMIN_NAME_DATIVE,
        "Имя Отчество": "Заурдин Джабраилович",
    }
    state = build_auto_layout_state(
        source,
        discover_placeholders(_pdf_text(source)),
        context,
    )

    fields_by_kind = {
        str(field.get("layout_kind")): field
        for field in state.get("fields") or []
    }
    assert {"document_header", "recipient", "greeting"} <= set(fields_by_kind)
    assert fields_by_kind["greeting"]["font_size"] >= 9
    assert fields_by_kind["greeting"]["value"] == "Уважаемый Заурдин Джабраилович!"
    assert fields_by_kind["recipient"]["value"] == (
        "Администрации муниципального образования «Энемское городское поселение»"
    )
    assert state["auto_layout"]["version"] == PDF_AUTO_LAYOUT_VERSION
    assert fields_by_kind["recipient"]["font_size"] == pytest.approx(11.0, abs=0.1)
    assert fields_by_kind["recipient"]["min_font_size"] == 9.5
    assert fields_by_kind["document_header"]["label"].startswith("Строка «№ {{id}}")
    assert fields_by_kind["document_header"]["label"] != "Номер и дата"


@pytest.mark.parametrize("font_size", [8, 9, 10, 11])
def test_auto_layout_uses_effective_minimum_font_for_composite_geometry(
    font_size: float,
) -> None:
    source = _placeholder_pdf(font_size=font_size)
    context = {
        "id": "125",
        "current_date": "28.07.2026",
        "ADM_NAME": LONG_ADMIN_NAME,
        "Имя Отчество": "Заурдин Джабраилович",
    }

    rendered = render_pdf_with_discovered_placeholders(
        source,
        discover_placeholders(_pdf_text(source)),
        context,
    )

    rendered_text = _pdf_text(rendered).replace("\u00a0", " ").replace("\u00ad", "-")
    assert "№ 125-ИП от 28.07.2026" in rendered_text


def test_auto_layout_expands_document_header_for_long_identifier() -> None:
    source = _placeholder_pdf()
    rendered = render_pdf_with_discovered_placeholders(
        source,
        discover_placeholders(_pdf_text(source)),
        {
            "id": "2026/08/10-125",
            "current_date": "28.07.2026",
            "ADM_NAME": LONG_ADMIN_NAME,
            "Имя Отчество": "Заурдин Джабраилович",
        },
    )

    rendered_text = _pdf_text(rendered).replace("\u00ad", "-")
    assert "2026/08/10-125-ИП" in rendered_text


def test_generic_compound_line_keeps_real_source_and_original_weight() -> None:
    font_file = _unicode_font_file()
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text(
        fitz.Point(65, 100),
        "{{company}} — {{region}}",
        fontsize=8,
        fontname="GenericLineSans",
        fontfile=font_file,
    )
    source = document.tobytes()
    document.close()

    state = build_auto_layout_state(
        source,
        discover_placeholders(_pdf_text(source)),
        {"company": "ООО Ромашка", "region": "Москва"},
    )
    field = next(item for item in state["fields"] if item["layout_kind"] == "composite_line")

    assert field["source_text"] == "{{company}} — {{region}}"
    assert field["variables"] == ["company", "region"]
    assert field["label"] == "Строка «{{company}} — {{region}}»"
    assert field["bold"] is False
    assert field["min_font_size"] == 8.0


def test_layout_error_identifies_real_source_page_and_variables() -> None:
    source = _highlighted_pdf()
    state = {
        "fields": [
            {
                "page": 0,
                "variable": "__composite__",
                "variables": ["id", "current_date"],
                "source_text": "№ {{id}} от {{current_date}}",
                "value": "№ ОЧЕНЬ-ДЛИННЫЙ-НОМЕР от 28.07.2026",
                "x": 100,
                "y": 100,
                "width": 20,
                "height": 8,
                "font_size": 10,
                "min_font_size": 10,
                "background": "#ffffff",
            }
        ]
    }

    with pytest.raises(PdfOverlayLayoutError) as raised:
        render_pdf(source, state)

    issue = raised.value.to_dict()
    assert issue["page"] == 1
    assert issue["source_text"] == "№ {{id}} от {{current_date}}"
    assert issue["variables"] == ["id", "current_date"]
    assert "Номер и дата" not in issue["message"]
    assert "Страница 1" in issue["message"]


def test_auto_layout_failure_falls_back_to_original_placeholder_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _placeholder_pdf()
    invalid_state = {
        "fields": [
            {
                "page": 0,
                "variables": ["id", "current_date"],
                "source_text": "№ {{id}}-ИП от {{current_date}}",
                "value": "значение, которое заведомо не поместится",
                "x": 65,
                "y": 90,
                "width": 5,
                "height": 5,
                "font_size": 10,
                "min_font_size": 10,
                "background": "#ffffff",
            }
        ]
    }
    monkeypatch.setattr(
        pdf_overlay_service,
        "build_auto_layout_state",
        lambda *_args, **_kwargs: invalid_state,
    )

    rendered = render_pdf_with_discovered_placeholders(
        source,
        discover_placeholders(_pdf_text(source)),
        {
            "id": "125",
            "current_date": "28.07.2026",
            "ADM_NAME": LONG_ADMIN_NAME,
            "Имя Отчество": "Заурдин Джабраилович",
        },
    )

    normalized = _pdf_text(rendered).replace("\u00a0", " ").replace("\u00ad", "-")
    assert "125" in normalized
    assert "28.07.2026" in normalized
    assert "{{id}}" not in normalized


def test_saved_v3_auto_layout_is_rebuilt_before_delivery() -> None:
    source = _placeholder_pdf(font_size=8)
    context = {
        "id": "125",
        "current_date": "28.07.2026",
        "ADM_NAME": LONG_ADMIN_NAME,
        "Имя Отчество": "Заурдин Джабраилович",
    }
    stale_state = build_auto_layout_state(
        source,
        discover_placeholders(_pdf_text(source)),
        context,
    )
    stale_state["auto_layout"]["version"] = "pdf-text-layout-v3"
    for field in stale_state["fields"]:
        field["width"] = 1
        field["height"] = 1

    rendered = _render_pdf_overlay(source, stale_state, context)

    normalized = _pdf_text(rendered).replace("\u00a0", " ").replace("\u00ad", "-")
    assert "№ 125-ИП от 28.07.2026" in normalized
    assert "{{" not in normalized


def test_layout_review_returns_actionable_fallback_instead_of_unknown_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _placeholder_pdf(font_size=8)
    context = {
        "id": "125",
        "current_date": "28.07.2026",
        "ADM_NAME": LONG_ADMIN_NAME,
        "Имя Отчество": "Заурдин Джабраилович",
    }
    monkeypatch.setattr(document_layout_service, "get_bytes", lambda _key: source)
    monkeypatch.setattr(
        document_layout_service.template_service,
        "cached_version_source_text",
        lambda _version: _pdf_text(source),
    )
    monkeypatch.setattr(
        document_layout_service,
        "_build_context",
        lambda *_args, **_kwargs: context,
    )

    def fail_candidate_render(_data: bytes, state: dict[str, object]) -> bytes:
        field = list(state.get("fields") or [])[0]
        assert isinstance(field, dict)
        raise PdfOverlayLayoutError(
            "Тестовая ошибка компоновки",
            field=field,
            value=str(field.get("value") or ""),
            reason="text_does_not_fit",
        )

    monkeypatch.setattr(document_layout_service, "render_pdf", fail_candidate_render)
    result = document_layout_service._review_template(
        template=SimpleNamespace(id="template-1", name="Layout PDF"),
        version=SimpleNamespace(
            id="version-1",
            filename="layout.pdf",
            storage_key="layout.pdf",
            editor_state=None,
        ),
        campaign=SimpleNamespace(id="campaign-1"),
        recipient=SimpleNamespace(id=1),
    )

    assert result["status"] == "fallback"
    assert result["fallback_used"] is True
    assert result["can_apply"] is False
    assert result["before_image"].startswith("data:image/png;base64,")
    assert result["issues"][0]["source_text"].startswith("№ {{id}}")
    assert sorted(result["issues"][0]["variables"]) == ["current_date", "id"]
    assert "Номер и дата" not in result["issues"][0]["message"]


def test_default_render_rebuilds_formal_lines_with_consistent_typography() -> None:
    source = _placeholder_pdf()
    context = {
        "id": "125",
        "current_date": "28.07.2026",
        "ADM_NAME": LONG_ADMIN_NAME,
        "ADM_NAME_1": LONG_ADMIN_NAME_DATIVE,
        "Имя Отчество": "Заурдин Джабраилович",
    }
    rendered = render_pdf_with_discovered_placeholders(
        source,
        discover_placeholders(_pdf_text(source)),
        context,
    )
    document = fitz.open(stream=rendered, filetype="pdf")
    try:
        page = document[0]
        rendered_text = page.get_text("text").replace("\u00a0", " ").replace("\u00ad", "-")
        spans = [
            span
            for block in page.get_text("dict").get("blocks", [])
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ]
        normalize = lambda value: str(value or "").replace("\u00a0", " ").replace("\u00ad", "-")
        header_spans = [span for span in spans if "125-ИП" in normalize(span.get("text"))]
        greeting_spans = [span for span in spans if "Уважаемый" in normalize(span.get("text"))]
        recipient_spans = [
            span
            for span in spans
            if "Администрации" in normalize(span.get("text"))
            or "поселение" in normalize(span.get("text"))
        ]

        assert "{{" not in rendered_text
        assert normalize(header_spans[0]["text"]) == (
            "№ 125-ИП от 28.07.2026"
        )
        assert "bold" in str(header_spans[0]["font"]).lower()
        assert len(greeting_spans) == 1
        assert normalize(greeting_spans[0]["text"]) == (
            "Уважаемый Заурдин Джабраилович!"
        )
        assert len({span["font"] for span in greeting_spans}) == 1
        assert recipient_spans
        assert "Администрации муниципального образования" in rendered_text
        assert len({round(float(span["size"]), 1) for span in recipient_spans}) == 1
        assert min(float(span["size"]) for span in recipient_spans) >= 9.5
        assert min(float(span["bbox"][0]) for span in recipient_spans) <= 320
    finally:
        document.close()


def test_auto_layout_composite_value_is_resolved_for_each_recipient() -> None:
    field = {
        "variable": "__composite__",
        "value_template": "№ {{id}}-ИП от {{current_date}}",
    }
    assert resolve_layout_field_value(
        field,
        {"id": "127", "current_date": "29.07.2026"},
    ) == "№ 127-ИП от 29.07.2026"
    assert "{{" not in resolve_layout_field_value(field, {"id": "127"})


def test_official_recipient_uses_dative_administration_name() -> None:
    field = {
        "variable": "ADM_NAME",
        "transform": "official_recipient_dative",
    }
    assert resolve_layout_field_value(
        field,
        {
            "ADM_NAME": LONG_ADMIN_NAME,
            "ADM_NAME_1": LONG_ADMIN_NAME_DATIVE,
        },
    ) == "Администрации муниципального образования «Энемское городское поселение»"
    assert resolve_layout_field_value(
        field,
        {"ADM_NAME": LONG_ADMIN_NAME},
    ) == "Администрации муниципального образования «Энемское городское поселение»"


def test_auto_layout_render_keeps_greeting_on_one_line() -> None:
    source = _placeholder_pdf()
    context = {
        "id": "125",
        "current_date": "28.07.2026",
        "ADM_NAME": LONG_ADMIN_NAME,
        "Имя Отчество": "Заурдин Джабраилович",
    }
    state = build_auto_layout_state(
        source,
        discover_placeholders(_pdf_text(source)),
        context,
    )
    rendered = render_pdf(source, state)
    document = fitz.open(stream=rendered, filetype="pdf")
    try:
        greeting = document[0].search_for("Уважаемый Заурдин Джабраилович!")
        assert len(greeting) == 1
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


def _pdf_text(data: bytes) -> str:
    document = fitz.open(stream=data, filetype="pdf")
    try:
        return "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()
