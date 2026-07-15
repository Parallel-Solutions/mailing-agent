from __future__ import annotations

from src.generator.templates import pdf_overlay as engine
from src.generator.templates.pdf_overlay import _Candidate, _Overlay, _build_html, _find_candidates
from src.generator.templates.pdf_overlay_compiler import _OUTGOING_NUMBER_LOOKAHEAD_RE


def test_pdf_fields_form_one_work_phrase() -> None:
    work_title = "разработке проекта местных нормативов градостроительного проектирования"
    text = (
        f"ООО предлагает выполнить работы по {work_title} MUN_R_NAME SUB_RF.\r\n"
        f"Выполнение работ по {work_title} MUN_R_NAME SUB_RF"
    )

    candidates, _ = _find_candidates(text, {"WORK_TITLE_1": work_title})
    work_phrases = [item for item in candidates if item.strategy == "combined_work_scope"]

    assert len(work_phrases) == 2
    assert all(item.fields == ("WORK_TITLE_1", "MUN_R_SCOPE_FRAGMENT") for item in work_phrases)
    assert all(item.template == "{{WORK_TITLE_1}} {{MUN_R_SCOPE_FRAGMENT}}" for item in work_phrases)


def test_broken_word_fields_are_rebuilt_as_one_outgoing_line(monkeypatch) -> None:
    monkeypatch.setattr(engine, "_OUTGOING_NUMBER_RE", _OUTGOING_NUMBER_LOOKAHEAD_RE)
    text = (
        "№Error! Unknown document property name. 101-КП от 12.05.2026"
        "Error! Unknown document property name. ADM_NAME"
    )

    candidates, cleanup = _find_candidates(text, {})
    outgoing = next(item for item in candidates if item.strategy == "combined_outgoing_line")

    assert outgoing.fields == ("OUTGOING_NUMBER", "DATE")
    assert outgoing.template == "№ {{OUTGOING_NUMBER}}-КП от {{DATE}}"
    assert cleanup


def test_pdf_compiler_uses_only_safe_inline_assets() -> None:
    class Page:
        @staticmethod
        def get_size() -> tuple[float, float]:
            return (595.0, 842.0)

    overlay = _Overlay(
        candidate=_Candidate(0, 3, ("ADM_NAME_1",), "{{ADM_NAME_1}}", "test", 1.0),
        char_boxes=((10.0, 10.0, 20.0, 20.0),),
        left=20.0,
        top=30.0,
        width=200.0,
        height=50.0,
        first_line_indent=0.0,
        font_family="Calibri",
        font_size=12.0,
        font_weight=700,
        color="#112244",
        line_height=14.0,
        min_font_size=8.0,
    )

    raw_html = _build_html(Page(), "data:image/png;base64,AAAA", [overlay], [])

    assert "http://" not in raw_html
    assert "https://" not in raw_html
    assert "{{ADM_NAME_1}}" in raw_html
    assert "data-adaptive-container" in raw_html
