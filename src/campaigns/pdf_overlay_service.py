"""Non-destructive editing of highlighted fields over an original PDF."""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import fitz

from src.campaigns import template_service
from src.campaigns.substitution_engine import (
    PlaceholderInfo,
    is_identifier_variable,
    resolve_context_value,
)
from src.infra.db import session_scope
from src.infra.models import MailTemplate, TemplateVersion
from src.infra.object_store import delete as delete_object
from src.infra.object_store import get_bytes, put_bytes

DATE_RE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")
VARIABLE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
FONT_FILES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
BOLD_FONT_FILES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
PDF_PAGE_MARGIN = 36.0
PDF_TEXTBOX_MIN_FONT_SIZE = 6.0
PDF_TEXTBOX_FONT_STEP = 0.25
PDF_TEXTBOX_LINE_HEIGHT = 1.0
PDF_REDACTION_HORIZONTAL_INSET = 0.25
PDF_REDACTION_VERTICAL_PADDING = 0.4
PDF_GEOMETRY_TOLERANCE = 0.25
KNOWN_FIELDS = {
    "ADM_NAME": ("ADM_NAME", "Получатель"),
    "HEAD_FIO": ("HEAD_FIO", "ФИО руководителя"),
    "(ая)": ("HEAD_SUFFIX", "Окончание обращения"),
}


class PdfOverlayLayoutError(ValueError):
    """Raised when a personalized PDF value cannot be rendered safely."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _field_identity(text: str) -> tuple[str, str]:
    clean = text.strip()
    if clean in KNOWN_FIELDS:
        return KNOWN_FIELDS[clean]
    if DATE_RE.fullmatch(clean):
        return "DATE", "Дата документа"
    if re.search(r"\d", clean) and re.search(r"[A-Za-zА-Яа-я]", clean):
        return "OUTGOING_NUMBER", "Исходящий номер"
    if VARIABLE_RE.fullmatch(clean):
        return clean, clean.replace("_", " ").title()
    return f"FIELD_{uuid.uuid4().hex[:8].upper()}", "Поле PDF"


def _is_yellow(fill: tuple[float, ...] | None) -> bool:
    return bool(fill and len(fill) >= 3 and fill[0] >= 0.8 and fill[1] >= 0.8 and fill[2] <= 0.25)


def _color_hex(color: int) -> str:
    return f"#{color & 0xFFFFFF:06x}"


def analyze_pdf(data: bytes) -> dict[str, Any]:
    """Find text covered by yellow rectangles and expose it as editable fields."""
    document = fitz.open(stream=data, filetype="pdf")
    pages: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    used_variables: set[str] = set()
    try:
        for page_index, page in enumerate(document):
            pages.append({"index": page_index, "width": page.rect.width, "height": page.rect.height})
            words = page.get_text("words")
            spans: list[dict[str, Any]] = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    spans.extend(line.get("spans", []))
            highlights = [drawing["rect"] for drawing in page.get_drawings() if _is_yellow(drawing.get("fill"))]
            for highlight_index, highlight in enumerate(highlights):
                probe = fitz.Rect(highlight.x0 - 1.5, highlight.y0 - 4, highlight.x1 + 1.5, highlight.y1 + 4)
                matches = [word for word in words if probe.contains(fitz.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2))]
                if not matches:
                    continue
                matches.sort(key=lambda word: (word[1], word[0]))
                source_text = " ".join(str(word[4]) for word in matches).strip()
                word_rect = fitz.Rect(matches[0][:4])
                for match in matches[1:]:
                    word_rect |= fitz.Rect(match[:4])
                span = next((item for item in spans if fitz.Rect(item["bbox"]).intersects(word_rect)), {})
                variable, label = _field_identity(source_text)
                if variable in used_variables:
                    variable = f"{variable}_{highlight_index + 1}"
                used_variables.add(variable)
                cover = highlight | word_rect
                origin = span.get("origin") or (word_rect.x0, word_rect.y1 - 2)
                fields.append({
                    "id": f"p{page_index}-f{highlight_index}",
                    "page": page_index,
                    "variable": variable,
                    "label": label,
                    "source_text": source_text,
                    "value": source_text,
                    "x": round(cover.x0, 3),
                    "y": round(cover.y0, 3),
                    "width": round(cover.width, 3),
                    "height": round(cover.height, 3),
                    "text_x": round(word_rect.x0, 3),
                    "baseline": round(float(origin[1]), 3),
                    "font_size": round(float(span.get("size") or max(8, word_rect.height * 0.72)), 2),
                    "bold": bool(int(span.get("flags") or 0) & 16),
                    "text_color": _color_hex(int(span.get("color") or 0)),
                    "background": "#ffff00",
                })
    finally:
        document.close()
    return {"page_count": len(pages), "pages": pages, "fields": fields}


def _is_pdf_document_template(template_type: str) -> bool:
    return template_service.normalize_file_template_type(template_type) == "document"


def _owned_pdf_version(template_id: str, owner_username: str) -> tuple[MailTemplate, TemplateVersion]:
    with session_scope() as session:
        template = session.get(MailTemplate, template_id)
        if (
            template is None
            or template.owner_username != owner_username
            or not _is_pdf_document_template(template.template_type)
        ):
            raise FileNotFoundError("Шаблон документа не найден")
        version = session.get(TemplateVersion, template.active_version_id) if template.active_version_id else None
        if version is None or not version.storage_key or not str(version.filename or "").lower().endswith(".pdf"):
            raise ValueError("Редактор полей доступен только для PDF-исходника")
        session.expunge(template)
        session.expunge(version)
        return template, version


def get_editor_state(template_id: str, owner_username: str) -> dict[str, Any]:
    _, version = _owned_pdf_version(template_id, owner_username)
    if version.editor_state:
        return deepcopy(version.editor_state)
    state = analyze_pdf(get_bytes(version.storage_key))
    with session_scope() as session:
        current = session.get(TemplateVersion, version.id)
        if current is not None and current.editor_state is None:
            current.editor_state = state
            session.flush()
    return state


def render_source_page(template_id: str, owner_username: str, page_index: int) -> dict[str, Any]:
    _, version = _owned_pdf_version(template_id, owner_username)
    document = fitz.open(stream=get_bytes(version.storage_key), filetype="pdf")
    try:
        if page_index < 0 or page_index >= document.page_count:
            raise FileNotFoundError("Страница PDF не найдена")
        content = document[page_index].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
    finally:
        document.close()
    return {"content": content, "filename": f"page-{page_index + 1}.png", "media_type": "image/png"}


def _rgb(value: str, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    clean = str(value or "").lstrip("#")
    if len(clean) != 6:
        return fallback
    try:
        return tuple(int(clean[index:index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _field_rect(field: dict[str, Any], *, prefix: str = "") -> fitz.Rect:
    x = float(field.get(f"{prefix}x", field["x"]))
    y = float(field.get(f"{prefix}y", field["y"]))
    width = float(field.get(f"{prefix}width", field["width"]))
    height = float(field.get(f"{prefix}height", field["height"]))
    return fitz.Rect(x, y, x + width, y + height)


def _redaction_rect(field: dict[str, Any], page_rect: fitz.Rect) -> fitz.Rect:
    source = _field_rect(field, prefix="redact_") if "redact_x" in field else _field_rect(field)
    inset = min(PDF_REDACTION_HORIZONTAL_INSET, max(0.0, source.width / 4))
    rect = fitz.Rect(
        source.x0 + inset,
        source.y0 - PDF_REDACTION_VERTICAL_PADDING,
        source.x1 - inset,
        source.y1 + PDF_REDACTION_VERTICAL_PADDING,
    )
    return rect & page_rect


def _rect_is_inside(container: fitz.Rect, candidate: fitz.Rect) -> bool:
    expanded = fitz.Rect(
        container.x0 - PDF_GEOMETRY_TOLERANCE,
        container.y0 - PDF_GEOMETRY_TOLERANCE,
        container.x1 + PDF_GEOMETRY_TOLERANCE,
        container.y1 + PDF_GEOMETRY_TOLERANCE,
    )
    return not candidate.is_empty and expanded.contains(candidate)


def _rects_materially_overlap(first: fitz.Rect, second: fitz.Rect) -> bool:
    intersection = first & second
    return (
        not intersection.is_empty
        and intersection.width > PDF_GEOMETRY_TOLERANCE
        and intersection.height > PDF_GEOMETRY_TOLERANCE
    )


def _font_file_for_field(field: dict[str, Any]) -> str | None:
    candidates = BOLD_FONT_FILES if bool(field.get("bold")) else FONT_FILES
    return next((path for path in candidates if Path(path).exists()), None)


def _insert_fitted_textbox(
    page: fitz.Page,
    field: dict[str, Any],
    rect: fitz.Rect,
    value: str,
) -> fitz.Rect:
    initial_font_size = max(
        PDF_TEXTBOX_MIN_FONT_SIZE,
        min(36.0, float(field.get("font_size") or 10)),
    )
    font_size = initial_font_size
    font_file = _font_file_for_field(field)
    font_name = (
        "CampaignFlowSansBold"
        if bool(field.get("bold")) and font_file
        else "CampaignFlowSans"
    )
    align = max(0, min(3, int(field.get("align") or 0)))

    while font_size + 1e-6 >= PDF_TEXTBOX_MIN_FONT_SIZE:
        kwargs: dict[str, Any] = {
            "fontsize": font_size,
            "color": _rgb(field.get("text_color", "#000000"), (0, 0, 0)),
            "overlay": True,
            "align": align,
            "lineheight": float(field.get("line_height") or PDF_TEXTBOX_LINE_HEIGHT),
        }
        if font_file:
            kwargs.update({"fontname": font_name, "fontfile": font_file})
        else:
            kwargs["fontname"] = "helv"
        spare_height = page.insert_textbox(rect, value, **kwargs)
        if spare_height >= 0:
            used_height = max(0.0, min(rect.height, rect.height - spare_height))
            return fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + used_height)
        font_size = round(font_size - PDF_TEXTBOX_FONT_STEP, 2)

    label = str(field.get("label") or field.get("variable") or "PDF field")
    raise PdfOverlayLayoutError(
        f"Текст поля «{label}» не помещается в заданную область даже при размере "
        f"{PDF_TEXTBOX_MIN_FONT_SIZE:g} pt."
    )


def _validate_rendered_field_rect(
    *,
    page_rect: fitz.Rect,
    field: dict[str, Any],
    rendered_rect: fitz.Rect,
    previous: list[tuple[dict[str, Any], fitz.Rect]],
) -> None:
    label = str(field.get("label") or field.get("variable") or "PDF field")
    if not _rect_is_inside(page_rect, rendered_rect):
        raise PdfOverlayLayoutError(f"Текст поля «{label}» выходит за границы страницы PDF.")
    for previous_field, previous_rect in previous:
        if not _rects_materially_overlap(rendered_rect, previous_rect):
            continue
        previous_label = str(
            previous_field.get("label") or previous_field.get("variable") or "PDF field"
        )
        raise PdfOverlayLayoutError(
            f"Поля «{previous_label}» и «{label}» перекрываются после подстановки."
        )


def render_pdf(data: bytes, state: dict[str, Any]) -> bytes:
    document = fitz.open(stream=data, filetype="pdf")
    try:
        changed_by_page: dict[int, list[dict[str, Any]]] = {}
        for field in state.get("fields") or []:
            if str(field.get("value", "")) != str(field.get("source_text", "")):
                changed_by_page.setdefault(int(field.get("page", 0)), []).append(field)
        for page_index, fields in changed_by_page.items():
            if page_index < 0 or page_index >= document.page_count:
                continue
            page = document[page_index]
            for field in fields:
                rect = _redaction_rect(field, page.rect)
                page.add_redact_annot(
                    rect,
                    fill=_rgb(field.get("background", "#ffff00"), (1, 1, 0)),
                )
            page.apply_redactions()
            rendered_fields: list[tuple[dict[str, Any], fitz.Rect]] = []
            for field in fields:
                value = str(field.get("value") or "")
                if not value:
                    continue
                textbox = _field_rect(field)
                label = str(field.get("label") or field.get("variable") or "PDF field")
                if not _rect_is_inside(page.rect, textbox):
                    raise PdfOverlayLayoutError(
                        f"Область поля «{label}» выходит за границы страницы PDF."
                    )
                rendered_rect = _insert_fitted_textbox(page, field, textbox, value)
                _validate_rendered_field_rect(
                    page_rect=page.rect,
                    field=field,
                    rendered_rect=rendered_rect,
                    previous=rendered_fields,
                )
                rendered_fields.append((field, rendered_rect))
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


def save_editor_state(template_id: str, owner_username: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    template, current = _owned_pdf_version(template_id, owner_username)
    state = get_editor_state(template_id, owner_username)
    incoming = {str(field.get("id")): field for field in fields if field.get("id")}
    for field in state.get("fields") or []:
        update = incoming.get(str(field.get("id")))
        if update is None:
            continue
        field["value"] = str(update.get("value", field.get("value", "")))[:500]
        if "font_size" in update:
            field["font_size"] = max(6.0, min(36.0, float(update["font_size"])))
    source_data = get_bytes(current.storage_key)
    pdf_data = render_pdf(source_data, state)
    version_id = _new_id()
    rendered_name = f"{Path(current.filename or template.name).stem}.pdf"
    rendered_key = f"template-library/{template_id}/{version_id}/delivery/{rendered_name}"
    put_bytes(rendered_key, pdf_data, content_type="application/pdf")
    try:
        with session_scope() as session:
            owned = session.get(MailTemplate, template_id)
            active = session.get(TemplateVersion, owned.active_version_id) if owned and owned.active_version_id else None
            if owned is None or owned.owner_username != owner_username or active is None:
                raise FileNotFoundError("Шаблон документа не найден")
            version = TemplateVersion(
                id=version_id,
                template_id=template_id,
                version_number=active.version_number + 1,
                subject=active.subject,
                body_html="",
                body_text="",
                variables=[{"name": field["variable"], "source": "pdf", "label": field["label"]} for field in state.get("fields") or []],
                storage_key=active.storage_key,
                filename=active.filename,
                rendered_pdf_storage_key=rendered_key,
                rendered_pdf_filename=rendered_name,
                source_text=active.source_text,
                source_sha256=active.source_sha256,
                text_extraction_status=active.text_extraction_status,
                text_extraction_error=active.text_extraction_error,
                text_extracted_at=active.text_extracted_at,
                editor_state=state,
                created_by=owner_username,
            )
            session.add(version)
            owned.active_version_id = version_id
            owned.status = "ready"
            session.flush()
            from src.campaigns.template_service import template_to_dict
            return template_to_dict(owned, version)
    except BaseException:
        delete_object(rendered_key)
        raise


def _find_text_instances(page: fitz.Page, needle: str) -> list[fitz.Rect]:
    if not needle:
        return []
    rects: list[fitz.Rect] = []
    for area in page.search_for(needle):
        rects.append(fitz.Rect(area))
    return rects


def _default_discovered_textbox_rect(
    page: fitz.Page,
    source_rect: fitz.Rect,
    *,
    font_size: float,
) -> fitz.Rect:
    required_height = max(source_rect.height + 1.5, font_size * 1.4)
    bottom = min(page.rect.y1, source_rect.y0 + required_height)
    return fitz.Rect(source_rect.x0, source_rect.y0, source_rect.x1, bottom)


def _next_content_top(
    page: fitz.Page,
    *,
    source_rect: fitz.Rect,
    right_edge: float,
) -> float | None:
    candidates: list[float] = []
    for word in page.get_text("words"):
        word_rect = fitz.Rect(word[:4])
        if word_rect.y0 <= source_rect.y1 + 1:
            continue
        if word_rect.x1 <= source_rect.x0 or word_rect.x0 >= right_edge:
            continue
        candidates.append(word_rect.y0)
    return min(candidates) if candidates else None


def _is_admin_name_placeholder(name: str) -> bool:
    return str(name or "").strip().upper() in {"ADM_NAME", "ADM_NAME_1"}


def _discovered_textbox_rect(
    page: fitz.Page,
    source_rect: fitz.Rect,
    placeholder: PlaceholderInfo,
    *,
    font_size: float,
) -> fitz.Rect:
    default_rect = _default_discovered_textbox_rect(page, source_rect, font_size=font_size)
    if not _is_admin_name_placeholder(placeholder.name):
        return default_rect

    right_edge = max(source_rect.x1, page.rect.x1 - PDF_PAGE_MARGIN)
    page_bottom = page.rect.y1 - PDF_PAGE_MARGIN
    next_top = _next_content_top(page, source_rect=source_rect, right_edge=right_edge)
    available_bottom = min(page_bottom, next_top - 4 if next_top is not None else page_bottom)
    bottom = max(source_rect.y1, available_bottom)
    return fitz.Rect(source_rect.x0, source_rect.y0, right_edge, bottom)


def render_pdf_with_discovered_placeholders(
    data: bytes,
    placeholders: list[PlaceholderInfo],
    context: dict[str, str],
) -> bytes:
    """Overlay bare/compound placeholders discovered in PDF text."""
    document = fitz.open(stream=data, filetype="pdf")
    fields: list[dict[str, Any]] = []
    try:
        for page_index in range(document.page_count):
            page = document[page_index]
            page_text = page.get_text("text")
            claimed_source_rects: list[fitz.Rect] = []
            ordered_placeholders = sorted(
                placeholders,
                key=lambda item: len(item.token),
                reverse=True,
            )
            for field_index, placeholder in enumerate(ordered_placeholders):
                value = resolve_context_value(context, placeholder.name)
                if not value or placeholder.token not in page_text:
                    continue
                for rect in _find_text_instances(page, placeholder.token):
                    if any(
                        _rects_materially_overlap(rect, claimed)
                        for claimed in claimed_source_rects
                    ):
                        continue
                    font_size = round(max(8.0, min(18.0, rect.height * 0.85)), 2)
                    textbox = _discovered_textbox_rect(
                        page,
                        rect,
                        placeholder,
                        font_size=font_size,
                    )
                    fields.append(
                        {
                            "id": f"p{page_index}-d{field_index}-{len(fields)}",
                            "page": page_index,
                            "variable": placeholder.name,
                            "label": placeholder.name,
                            "source_text": placeholder.token,
                            "value": value,
                            "redact_x": round(rect.x0, 3),
                            "redact_y": round(rect.y0, 3),
                            "redact_width": round(rect.width, 3),
                            "redact_height": round(rect.height, 3),
                            "x": round(textbox.x0, 3),
                            "y": round(textbox.y0, 3),
                            "width": round(textbox.width, 3),
                            "height": round(textbox.height, 3),
                            "font_size": font_size,
                            "bold": False,
                            "align": 2 if is_identifier_variable(placeholder.name) else 0,
                            "text_color": "#000000",
                            "background": "#ffffff",
                        }
                    )
                    claimed_source_rects.append(rect)
    finally:
        document.close()
    if not fields:
        return data
    return render_pdf(data, {"fields": fields})
