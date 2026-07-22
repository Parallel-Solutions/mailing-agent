"""Convert uploaded documents into visual email HTML templates."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from src.campaigns import template_ai, template_service
from src.generator.generation.docxjs_converter import (
    convert_docx_to_html_result,
    convert_docx_to_pdf_bytes,
)
from src.generator.generation.import_utils import clamp_viewport_width, png_to_data_uri
from src.generator.generation.import_visual_qa import (
    HtmlCandidate,
    pick_best_candidate,
    render_and_score,
    render_html_to_png,
)
from src.generator.generation.pdf_fixed_layout import extract_fixed_layout
from src.utils.config import settings

logger = logging.getLogger(__name__)

_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_TAG_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_ON_EVENT_RE = re.compile(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"\s(href|src)\s*=\s*(['\"])\s*javascript:.*?\2", re.IGNORECASE)
_CONTENT_WIDTH_RE = re.compile(r'data-content-width=["\'](\d+)["\']', re.IGNORECASE)
_FIXED_LAYOUT_RE = re.compile(r'data-layout=["\']fixed["\']', re.IGNORECASE)
_DATA_URI_IMG_RE = re.compile(
    r'(<img\b[^>]*\bsrc=["\'])(data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=]+))(["\'])',
    re.IGNORECASE,
)
_BRACKET_LABEL_RE = re.compile(r"\[[^\]]+\]")
_IMPORT_ALLOWED_SUFFIXES = {".docx", ".pdf", ".html", ".htm", ".txt"}
_EMAIL_VIEWPORT_WIDTH = 640
_MAX_VISION_PAGES = 5
_PDF_PREVIEW_SCALE = 2.0

_IMPORT_VISION_SYSTEM = (
    "Ты конвертируешь загруженный документ (PDF/скан/DOCX) в редактируемый HTML-шаблон "
    "email-рассылки на русском. "
    'Верни только JSON: {"name":"...","subject":"...","body_html":"..."} '
    'body_html — table-based HTML с указанной max-width, только inline-стили '
    '(style="..."), без <style> и <script>. '
    "КРИТИЧНО: воспроизведи layout со скриншота и черновика максимально близко — "
    "колонки, таблицы, цвета, шрифты, логотипы, отступы, иерархию заголовков. "
    "Не переосмысляй дизайн и не упрощай в generic newsletter. "
    "Сохраняй цвета/размеры/отступы из черновика HTML, если они уже близки к скриншоту. "
    "Если в черновике уже есть кнопки (background-color + <a>), callout-блоки "
    "(background-color / border-left) и цветные span — сохрани их структуру и цвета, "
    "не заменяй на plain <p> и не превращай в картинки. "
    "Используй data-uri картинки из черновика как есть (не заменяй их). "
    "Текст должен быть редактируемым в тегах <p>/<h1-h3>/<td>/<span>/<a>, не одной картинкой страницы. "
    "Отдельные изображения (логотипы и т.п.) допустимы как <img>. "
    "Нельзя возвращать целую страницу как единственный <img>. "
    "Для PDF/сканов: OCR-текст правь только при явных ошибках распознавания; "
    "не выдумывай абзацы, которых нет на скриншоте. "
    "Сохраняй плейсхолдеры {{company}}, {{contact_name}}, {{email}}, {{region}}, "
    "{{Имя}}, {{Отчество}}, {{Компания}} если они есть в тексте."
)
_IMPORT_DRAFT_CHAR_LIMIT = 24000
_IMPORT_VISION_MAX_TOKENS = 12000
_IMPORT_PLAN_MAX_TOKENS = 2500
_IMPORT_MODEL_PREFERENCE = ("gpt-4.1", "gpt-4o")
_PLAN_DONE_CONFIDENCE = 0.85
_STALE_ROUNDS_LIMIT = 2

_IMPORT_PLAN_SYSTEM = (
    "Ты аудитор конвертации документа (PDF/DOCX) в HTML email-рассылку на русском. "
    "Сравни скриншоты исходного документа и текущего HTML-кандидата. "
    'Верни только JSON: {"done":boolean,"confidence":number,"issues":string[],"priorities":string[],"notes":string}. '
    "done=true только если layout, цвета, типографика и структура уже достаточно близки к исходнику. "
    "issues — конкретные расхождения (колонки, отступы, кнопки, логотипы, шрифты). "
    "priorities — что исправить в первую очередь."
)

_IMPORT_EXECUTE_SYSTEM = (
    "Ты конвертируешь загруженный документ (PDF/скан/DOCX) в редактируемый HTML-шаблон "
    "email-рассылки на русском, применяя план правок. "
    'Верни только JSON: {"name":"...","subject":"...","body_html":"..."} '
    'body_html — table-based HTML с указанной max-width, только inline-стили '
    '(style="..."), без <style> и <script>. '
    "КРИТИЧНО: воспроизведи layout со скриншота и черновика максимально близко — "
    "колонки, таблицы, цвета, шрифты, логотипы, отступы, иерархию заголовков. "
    "Исправь issues/priorities из плана аудита. "
    "Не переосмысляй дизайн и не упрощай в generic newsletter. "
    "Сохраняй цвета/размеры/отступы из черновика HTML, если они уже близки к скриншоту. "
    "Если в черновике уже есть кнопки (background-color + <a>), callout-блоки "
    "(background-color / border-left) и цветные span — сохрани их структуру и цвета, "
    "не заменяй на plain <p> и не превращай в картинки. "
    "Используй data-uri картинки из черновика как есть (не заменяй их). "
    "Текст должен быть редактируемым в тегах <p>/<h1-h3>/<td>/<span>/<a>, не одной картинкой страницы. "
    "Отдельные изображения (логотипы и т.п.) допустимы как <img>. "
    "Нельзя возвращать целую страницу как единственный <img>. "
    "Для PDF/сканов: OCR-текст правь только при явных ошибках распознавания; "
    "не выдумывай абзацы, которых нет на скриншоте. "
    "Сохраняй плейсхолдеры {{company}}, {{contact_name}}, {{email}}, {{region}}, "
    "{{Имя}}, {{Отчество}}, {{Компания}} если они есть в тексте."
)


@dataclass
class _ImportRefinementState:
    round: int = 0
    best_html: str = ""
    best_score: float = 0.0
    spent_usd: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""


def _plain_text_from_html(html: str) -> str:
    without_scripts = _SCRIPT_TAG_RE.sub(" ", html or "")
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return re.sub(r"\s+", " ", without_tags).strip()


def _detect_content_width(html: str, default: int = _EMAIL_VIEWPORT_WIDTH) -> int:
    match = _CONTENT_WIDTH_RE.search(html or "")
    if match:
        return clamp_viewport_width(int(match.group(1)), default=default)
    max_width_match = re.search(r"max-width\s*:\s*(\d+)px", html or "", re.IGNORECASE)
    if max_width_match:
        return clamp_viewport_width(int(max_width_match.group(1)), default=default)
    return clamp_viewport_width(default)


def normalize_email_html(
    html: str,
    *,
    preserve_inner_layout: bool = False,
    max_width: int | None = None,
) -> str:
    cleaned = (html or "").strip()
    width = clamp_viewport_width(max_width if max_width is not None else _EMAIL_VIEWPORT_WIDTH)
    if not cleaned:
        return (
            f'<table width="100%" cellpadding="0" cellspacing="0" role="presentation" '
            f'class="main-body" data-content-width="{width}" '
            f'style="width:100%;max-width:{width}px;margin:0 auto;background:#ffffff">'
            "<tr><td><p>&nbsp;</p></td></tr></table>"
        )

    cleaned = _SCRIPT_TAG_RE.sub("", cleaned)
    is_fixed_layout = bool(_FIXED_LAYOUT_RE.search(cleaned))
    if not is_fixed_layout:
        cleaned = _STYLE_TAG_RE.sub("", cleaned)
    cleaned = _ON_EVENT_RE.sub("", cleaned)
    cleaned = _JAVASCRIPT_URL_RE.sub("", cleaned)

    has_table = bool(re.search(r"<table\b", cleaned, re.IGNORECASE))
    rich_layout = preserve_inner_layout or is_fixed_layout or bool(
        re.search(r"docx-wrapper|section\.docx|data-page=|docx-import-root", cleaned, re.IGNORECASE)
    )
    if has_table and not rich_layout and re.search(r'class=["\'][^"\']*main-body', cleaned, re.IGNORECASE):
        if "data-content-width=" not in cleaned:
            cleaned = cleaned.replace("<table", f'<table data-content-width="{width}"', 1)
        return cleaned
    if has_table and not rich_layout:
        if "data-content-width=" not in cleaned:
            cleaned = cleaned.replace("<table", f'<table data-content-width="{width}"', 1)
        return cleaned

    cell_padding = "padding:0" if is_fixed_layout else "padding:8px 16px"
    wrapper_open = (
        f'<table width="100%" cellpadding="0" cellspacing="0" role="presentation" '
        f'class="main-body" data-content-width="{width}" '
        f'style="width:100%;max-width:{width}px;margin:0 auto;background:#ffffff">'
        f'<tr><td class="cell" style="{cell_padding}">'
    )
    wrapper_close = "</td></tr></table>"
    return f"{wrapper_open}{cleaned}{wrapper_close}"


def _is_fixed_layout_html(html: str) -> bool:
    return bool(_FIXED_LAYOUT_RE.search(html or ""))


def _needs_style_inlining(html: str) -> bool:
    if _STYLE_TAG_RE.search(html):
        return True
    class_count = len(re.findall(r"\sclass=", html, re.IGNORECASE))
    style_count = len(re.findall(r"\sstyle=", html, re.IGNORECASE))
    return class_count >= 2 and style_count <= max(2, class_count // 4)


def _split_head_styles(html: str) -> tuple[str, str]:
    styles: list[str] = []
    for match in _STYLE_TAG_RE.finditer(html):
        styles.append(match.group(0))
    body = _STYLE_TAG_RE.sub("", html).strip() if styles else html
    return "".join(styles), body


def _orphan_bracket_labels_in_html(html: str) -> list[str]:
    button_labels = set()
    for match in re.finditer(
        r'data-shape="button"[^>]*>.*?</(?:p|td|div|table)>',
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        block = match.group(0)
        for label in _BRACKET_LABEL_RE.findall(block):
            button_labels.add(label)

    orphans: list[str] = []
    for match in re.finditer(r"<(?:p|h[1-6])[^>]*>.*?</(?:p|h[1-6])>", html or "", flags=re.IGNORECASE | re.DOTALL):
        block = match.group(0)
        if 'data-shape="button"' in block.lower():
            continue
        labels = _BRACKET_LABEL_RE.findall(block)
        for label in labels:
            if label not in button_labels:
                orphans.append(label)
    return orphans


def _remove_orphan_bracket_button_labels(html: str) -> str:
    orphans = set(_orphan_bracket_labels_in_html(html))
    if not orphans:
        return html

    def replace_paragraph(match: re.Match[str]) -> str:
        block = match.group(0)
        if 'data-shape="button"' in block.lower():
            return block
        labels = _BRACKET_LABEL_RE.findall(block)
        if not any(label in orphans for label in labels):
            return block
        plain = re.sub(r"<[^>]+>", " ", block)
        plain = re.sub(r"\s+", " ", plain).strip()
        if "text-decoration:underline" in block.lower() or plain in orphans or plain in {
            label.strip("[]") for label in orphans
        }:
            return ""
        return block

    return re.sub(
        r"<(?:p|h[1-6])[^>]*>.*?</(?:p|h[1-6])>",
        replace_paragraph,
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    )


def _prepare_import_html(html: str, *, content_width: int | None = None) -> str:
    fragment = (html or "").strip()
    width = _detect_content_width(fragment, default=content_width or _EMAIL_VIEWPORT_WIDTH)
    if content_width is not None:
        width = clamp_viewport_width(content_width)
    if not fragment:
        return normalize_email_html("", max_width=width)

    if _is_fixed_layout_html(fragment):
        return normalize_email_html(fragment, preserve_inner_layout=True, max_width=width)

    head_styles, body = _split_head_styles(fragment)
    if _needs_style_inlining(body) or head_styles:
        from src.generator.generation.html_style_inliner import inline_html_styles

        inlined = inline_html_styles(
            body,
            head_styles=head_styles,
            viewport_width=width,
        )
        if inlined:
            body = inlined

    body = _remove_orphan_bracket_button_labels(body)
    return normalize_email_html(body, preserve_inner_layout=True, max_width=width)


def _paragraphs_to_html(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "<p>&nbsp;</p>"
    return "".join(f"<p>{escape(line)}</p>" for line in lines)


def _is_full_page_image_only(html: str) -> bool:
    plain = _plain_text_from_html(html)
    img_count = len(re.findall(r"<img\b", html, re.IGNORECASE))
    has_text_tags = bool(re.search(r"<(?:p|h[1-6]|li|span|a)\b", html, re.IGNORECASE))
    return img_count >= 1 and not has_text_tags and len(plain) < 40


def _pdf_page_pngs(data: bytes, *, max_pages: int = _MAX_VISION_PAGES) -> list[bytes]:
    try:
        import fitz
    except ImportError:
        return []
    pngs: list[bytes] = []
    try:
        document = fitz.open(stream=data, filetype="pdf")
        try:
            for page_index in range(min(document.page_count, max_pages)):
                page = document.load_page(page_index)
                pix = page.get_pixmap(matrix=fitz.Matrix(_PDF_PREVIEW_SCALE, _PDF_PREVIEW_SCALE), alpha=False)
                pngs.append(pix.tobytes("png"))
        finally:
            document.close()
    except Exception as exc:
        logger.warning("template_import_pdf_preview_failed error=%s", exc)
    return pngs


def _qa_metadata(picked: Any) -> dict[str, Any]:
    return {
        "winner": picked.name,
        "winner_score": round(float(picked.score), 4),
        "candidate_scores": dict(picked.scores or {}),
    }


def _import_pdf(data: bytes, *, stem: str) -> tuple[str, str, str, str, dict[str, Any] | None]:
    draft_html, _plain_text, _preview_pngs, width = extract_fixed_layout(
        data,
        content_width=_EMAIL_VIEWPORT_WIDTH,
    )
    if not draft_html.strip():
        raise ValueError("Не удалось обработать PDF")
    body = _prepare_import_html(draft_html, content_width=width)
    if _is_full_page_image_only(body):
        raise ValueError("PDF содержит только изображение страницы без редактируемого текста")
    return stem, "Тема письма", body, "fixed_layout", None


def _import_docx(data: bytes, *, stem: str) -> tuple[str, str, str, str, dict[str, Any] | None]:
    candidates: list[HtmlCandidate] = []
    reference_pngs: list[bytes] = []
    content_width = _EMAIL_VIEWPORT_WIDTH

    pdf_bytes = convert_docx_to_pdf_bytes(data)
    if pdf_bytes:
        reference_pngs = _pdf_page_pngs(pdf_bytes)
        draft_html, _plain_text, preview_pngs, width = extract_fixed_layout(
            pdf_bytes,
            content_width=_EMAIL_VIEWPORT_WIDTH,
        )
        if draft_html.strip():
            fixed_html = _prepare_import_html(draft_html, content_width=width)
            if not _is_full_page_image_only(fixed_html):
                candidates.append(HtmlCandidate(name="fixed_layout", html=fixed_html))
                content_width = width
                if not reference_pngs and preview_pngs:
                    reference_pngs = preview_pngs

    docx_result = convert_docx_to_html_result(data)
    if docx_result and docx_result.html.strip():
        docx_html = _prepare_import_html(docx_result.html, content_width=docx_result.content_width)
        if not _is_full_page_image_only(docx_html):
            candidates.append(HtmlCandidate(name="docxjs", html=docx_html, prefer_on_tie=True))
            content_width = docx_result.content_width
            if not reference_pngs:
                rendered = render_html_to_png(docx_html, viewport_width=docx_result.content_width)
                if rendered:
                    reference_pngs = [rendered]

    if not candidates:
        raise ValueError("Не удалось конвертировать DOCX")

    if len(candidates) == 1:
        only = candidates[0]
        return stem, "Тема письма", only.html, only.name, None

    picked = pick_best_candidate(candidates, reference_pngs, viewport_width=content_width)
    if picked is None:
        only = candidates[0]
        return stem, "Тема письма", only.html, only.name, None

    return stem, "Тема письма", picked.html, picked.name, _qa_metadata(picked)


def _import_html(data: bytes, *, stem: str) -> tuple[str, str, str, str, dict[str, Any] | None]:
    raw = data.decode("utf-8", errors="replace").strip()
    if not raw:
        raise ValueError("HTML-файл пуст")
    body = _prepare_import_html(raw)
    return stem, "Тема письма", body, "html", None


def _import_txt(data: bytes, *, stem: str) -> tuple[str, str, str, str, dict[str, Any] | None]:
    plain_text = data.decode("utf-8", errors="replace").strip()
    if not plain_text:
        raise ValueError("Текстовый файл пуст")
    body = _prepare_import_html(_paragraphs_to_html(plain_text))
    return stem, "Тема письма", body, "txt", None


def _convert_to_html(filename: str, data: bytes) -> tuple[str, str, str, str, dict[str, Any] | None]:
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem or "Шаблон"

    if suffix == ".pdf":
        return _import_pdf(data, stem=stem)
    if suffix == ".docx":
        return _import_docx(data, stem=stem)
    if suffix in {".html", ".htm"}:
        return _import_html(data, stem=stem)
    if suffix == ".txt":
        return _import_txt(data, stem=stem)

    raise ValueError(f"Неподдерживаемый формат: {suffix or '(без расширения)'}")


def _default_import_model() -> str:
    models = template_ai.list_models()
    ids = [str(item.get("id") or "").strip() for item in models if item.get("id")]
    for preferred in _IMPORT_MODEL_PREFERENCE:
        if preferred in ids:
            return preferred
    for model_id in reversed(ids):
        if "mini" not in model_id.lower():
            return model_id
    return ids[0] if ids else "gpt-4o-mini"


def _collect_draft_image_uris(html: str, *, limit: int = 8) -> list[str]:
    found = re.findall(
        r'src=["\'](data:image/(?:png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=]+)["\']',
        html or "",
        flags=re.IGNORECASE,
    )
    return found[:limit]


def _import_refinement_limits() -> tuple[int, float, float]:
    max_rounds = int(getattr(settings, "template_import_max_rounds", 10) or 10)
    max_cost_usd = float(getattr(settings, "template_import_max_cost_usd", 1.5) or 1.5)
    target_similarity = float(getattr(settings, "template_import_target_similarity", 0.97) or 0.97)
    return max(1, max_rounds), max(0.01, max_cost_usd), max(0.0, min(1.0, target_similarity))


def _import_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _import_refinement_metadata(state: _ImportRefinementState) -> dict[str, Any]:
    return {
        "rounds": state.round,
        "best_score": round(state.best_score, 4),
        "spent_usd": round(state.spent_usd, 4),
        "stop_reason": state.stop_reason or "max_rounds",
        "source": "vision_iterative",
        "trace": state.trace,
    }


def _finalize_import_refinement(
    import_refinement: dict[str, Any] | None,
    *,
    selected_source: str,
    qa: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = dict(import_refinement or {})
    result["selected_source"] = selected_source
    if qa:
        result["qa"] = qa
    if import_refinement:
        result["available"] = True
    else:
        result.setdefault("available", False)
        result.setdefault("stop_reason", "not_run")
    return result


def _estimate_next_vision_cost(
    model: str,
    *,
    image_count: int,
    prompt_tokens: int = 4000,
    completion_tokens: int = 800,
) -> float:
    return template_ai.estimate_llm_cost_usd(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        image_count=image_count,
    )


def _build_import_plan_user_text(
    *,
    filename: str,
    plain_text: str,
    draft_html: str,
    current_html: str,
    layout_hint: str,
    content_width: int,
    round_num: int,
    score: float,
    previous_issues: list[str],
) -> str:
    draft_images = _collect_draft_image_uris(draft_html)
    images_note = (
        "Data-URI из черновика (используй как есть):\n" + "\n".join(draft_images[:6])
        if draft_images
        else "Отдельных data-URI в черновике нет."
    )
    issues_note = "\n".join(f"- {item}" for item in previous_issues[:12]) or "(нет)"
    return (
        f"Файл: {filename}\n"
        f"Раунд: {round_num}\n"
        f"Целевая max-width письма: {clamp_viewport_width(content_width)}px\n"
        f"Подсказка: {layout_hint or '(нет)'}\n"
        f"PNG similarity текущего HTML vs исходник: {score:.3f}\n\n"
        f"Прошлые issues:\n{issues_note}\n\n"
        f"{images_note}\n\n"
        f"Текущий HTML-кандидат:\n{current_html[:_IMPORT_DRAFT_CHAR_LIMIT]}\n\n"
        f"Черновик HTML:\n{draft_html[:_IMPORT_DRAFT_CHAR_LIMIT]}\n\n"
        f"Извлечённый текст:\n{plain_text[:_IMPORT_DRAFT_CHAR_LIMIT]}\n\n"
        "Скриншоты: исходный документ и рендер текущего HTML. "
        "Составь план правок для максимального совпадения с исходником."
    )


def _build_import_execute_user_text(
    *,
    filename: str,
    plain_text: str,
    draft_html: str,
    current_html: str,
    layout_hint: str,
    content_width: int,
    round_num: int,
    score: float,
    plan: dict[str, Any],
) -> str:
    draft_images = _collect_draft_image_uris(draft_html)
    images_note = (
        "Data-URI из черновика (используй как есть):\n" + "\n".join(draft_images[:6])
        if draft_images
        else "Отдельных data-URI в черновике нет."
    )
    issues = _import_string_list(plan.get("issues"))
    priorities = _import_string_list(plan.get("priorities"))
    notes = str(plan.get("notes") or "").strip()
    return (
        f"Файл: {filename}\n"
        f"Раунд: {round_num}\n"
        f"Целевая max-width письма: {clamp_viewport_width(content_width)}px\n"
        f"Подсказка: {layout_hint or '(нет)'}\n"
        f"PNG similarity до правок: {score:.3f}\n\n"
        f"План аудита:\n"
        f"issues:\n" + ("\n".join(f"- {item}" for item in issues) or "- (нет)") + "\n"
        f"priorities:\n" + ("\n".join(f"- {item}" for item in priorities) or "- (нет)") + "\n"
        f"notes: {notes or '(нет)'}\n\n"
        f"{images_note}\n\n"
        f"Текущий HTML (база для правок):\n{current_html[:_IMPORT_DRAFT_CHAR_LIMIT]}\n\n"
        f"Черновик HTML:\n{draft_html[:_IMPORT_DRAFT_CHAR_LIMIT]}\n\n"
        f"Извлечённый текст:\n{plain_text[:_IMPORT_DRAFT_CHAR_LIMIT]}\n\n"
        "Скриншоты: исходный документ и рендер текущего HTML. "
        "Примени план и верни улучшенный редактируемый email HTML."
    )


def _refine_import_iteratively(
    *,
    filename: str,
    plain_text: str,
    draft_html: str,
    preview_pngs: list[bytes],
    layout_hint: str = "",
    content_width: int = _EMAIL_VIEWPORT_WIDTH,
    force_refinement: bool = False,
    layout_gaps: list[str] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not preview_pngs and not draft_html.strip():
        return None, None

    max_rounds, max_cost_usd, target_similarity = _import_refinement_limits()
    width = clamp_viewport_width(content_width)
    model = _default_import_model()
    current_html = _prepare_import_html(draft_html, content_width=width) if draft_html.strip() else draft_html
    state = _ImportRefinementState(best_html=current_html)
    if preview_pngs:
        state.best_score, _ = render_and_score(current_html, preview_pngs, viewport_width=width)

    stale_rounds = 0
    gap_issues = _import_string_list(layout_gaps)
    previous_issues: list[str] = list(gap_issues)
    last_payload: dict[str, Any] | None = None
    needs_layout_polish = bool(gap_issues)
    min_rounds = 1 if (needs_layout_polish or force_refinement) else 0

    if (
        not force_refinement
        and not needs_layout_polish
        and state.best_score >= target_similarity
        and preview_pngs
    ):
        state.stop_reason = "target_score"
        return (
            {"body_html": state.best_html, "name": Path(filename).stem, "subject": "Тема письма"},
            _import_refinement_metadata(state),
        )

    for round_num in range(1, max_rounds + 1):
        state.round = round_num

        if state.spent_usd >= max_cost_usd:
            state.stop_reason = "budget"
            break

        score, candidate_png = (
            render_and_score(current_html, preview_pngs, viewport_width=width)
            if preview_pngs
            else (0.0, None)
        )
        if score > state.best_score:
            state.best_score = score
            state.best_html = current_html
            stale_rounds = 0
        elif round_num > 1:
            stale_rounds += 1

        if preview_pngs and state.best_score >= target_similarity and round_num >= min_rounds:
            state.stop_reason = "target_score"
            break

        if stale_rounds >= _STALE_ROUNDS_LIMIT and round_num > 1:
            state.stop_reason = "no_improvement"
            break

        image_urls = [png_to_data_uri(png) for png in preview_pngs[:_MAX_VISION_PAGES]]
        if candidate_png:
            image_urls.append(png_to_data_uri(candidate_png))

        plan_cost = _estimate_next_vision_cost(model, image_count=len(image_urls), completion_tokens=400)
        if state.spent_usd + plan_cost > max_cost_usd:
            state.stop_reason = "budget"
            break

        plan_user = _build_import_plan_user_text(
            filename=filename,
            plain_text=plain_text,
            draft_html=draft_html,
            current_html=current_html,
            layout_hint=layout_hint,
            content_width=content_width,
            round_num=round_num,
            score=score,
            previous_issues=previous_issues,
        )
        plan: dict[str, Any] = {}
        try:
            plan_result = template_ai._call_vision_llm_tracked(  # noqa: SLF001
                model,
                _IMPORT_PLAN_SYSTEM,
                plan_user,
                image_urls,
                max_tokens=_IMPORT_PLAN_MAX_TOKENS,
            )
            state.spent_usd += plan_result.estimated_cost_usd
            plan = plan_result.payload
        except Exception as exc:
            logger.warning(
                "template_import_plan_failed filename=%s round=%s error=%s",
                filename,
                round_num,
                exc,
            )
            state.trace.append({"round": round_num, "phase": "plan", "error": str(exc)})
            continue

        plan_done = bool(plan.get("done"))
        plan_confidence = float(plan.get("confidence") or 0.0)
        previous_issues = _import_string_list(plan.get("issues"))
        state.trace.append(
            {
                "round": round_num,
                "phase": "plan",
                "score": round(score, 4),
                "done": plan_done,
                "confidence": plan_confidence,
                "issues": previous_issues[:8],
                "cost_usd": round(plan_result.estimated_cost_usd, 6),
            }
        )

        if plan_done and plan_confidence >= _PLAN_DONE_CONFIDENCE:
            state.stop_reason = "plan_done"
            break

        execute_cost = _estimate_next_vision_cost(
            model,
            image_count=len(image_urls),
            completion_tokens=2500,
        )
        if state.spent_usd + execute_cost > max_cost_usd:
            state.stop_reason = "budget"
            break

        execute_user = _build_import_execute_user_text(
            filename=filename,
            plain_text=plain_text,
            draft_html=draft_html,
            current_html=current_html,
            layout_hint=layout_hint,
            content_width=content_width,
            round_num=round_num,
            score=score,
            plan=plan,
        )
        try:
            execute_result = template_ai._call_vision_llm_tracked(  # noqa: SLF001
                model,
                _IMPORT_EXECUTE_SYSTEM,
                execute_user,
                image_urls,
                max_tokens=_IMPORT_VISION_MAX_TOKENS,
            )
            state.spent_usd += execute_result.estimated_cost_usd
            last_payload = execute_result.payload
        except Exception as exc:
            logger.warning(
                "template_import_execute_failed filename=%s round=%s error=%s",
                filename,
                round_num,
                exc,
            )
            state.trace.append({"round": round_num, "phase": "execute", "error": str(exc)})
            continue

        new_html_raw = str(last_payload.get("body_html") or "").strip()
        round_score = score
        if new_html_raw:
            new_html = _prepare_import_html(new_html_raw, content_width=width)
            if not _is_full_page_image_only(new_html):
                current_html = new_html
                if preview_pngs:
                    round_score, _ = render_and_score(current_html, preview_pngs, viewport_width=width)
                    if round_score > state.best_score:
                        state.best_score = round_score
                        state.best_html = current_html
                        stale_rounds = 0
                    else:
                        stale_rounds += 1

        state.trace.append(
            {
                "round": round_num,
                "phase": "execute",
                "score": round(round_score, 4),
                "best_score": round(state.best_score, 4),
                "cost_usd": round(execute_result.estimated_cost_usd, 6),
            }
        )

        if preview_pngs and state.best_score >= target_similarity and round_num >= min_rounds:
            state.stop_reason = "target_score"
            break
    else:
        if not state.stop_reason:
            state.stop_reason = "max_rounds"

    if not state.best_html.strip() and not last_payload:
        return None, _import_refinement_metadata(state)

    payload: dict[str, Any] = dict(last_payload or {})
    payload["body_html"] = state.best_html
    payload.setdefault("name", Path(filename).stem)
    payload.setdefault("subject", "Тема письма")
    return payload, _import_refinement_metadata(state)


def _externalize_data_uri_images(owner_username: str, template: dict[str, Any]) -> dict[str, Any]:
    template_id = str(template.get("id") or "")
    version = template.get("version") or {}
    body_html = str(version.get("body_html") or "")
    if not template_id or "data:image/" not in body_html.lower():
        return template
    if _FIXED_LAYOUT_RE.search(body_html):
        return template

    counter = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal counter
        prefix, _full, fmt, b64, suffix = match.groups()
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return match.group(0)
        if len(raw) < 32:
            return match.group(0)
        counter += 1
        ext = "jpg" if fmt.lower() in {"jpeg", "jpg"} else fmt.lower()
        asset = template_service.upload_template_asset(
            template_id,
            owner_username,
            filename=f"import-{counter}.{ext}",
            data=raw,
            content_type=f"image/{'jpeg' if ext == 'jpg' else ext}",
        )
        if not asset:
            return match.group(0)
        return f"{prefix}{asset['url']}{suffix}"

    updated_html = _DATA_URI_IMG_RE.sub(_replace, body_html)
    if updated_html == body_html:
        return template

    saved = template_service.save_version(
        template_id,
        owner_username,
        body_html=updated_html,
        body_text=_plain_text_from_html(updated_html),
        editor_state=version.get("editor_state"),
        subject=version.get("subject"),
    )
    return saved or template


def regenerate_imported_template(owner_username: str, template_id: str) -> dict[str, Any]:
    """Re-run vision refinement on an already imported visual email template."""
    template = template_service.get_template(template_id, owner_username)
    if not template:
        raise FileNotFoundError("Шаблон не найден")

    version = template.get("version") or {}
    editor_state = version.get("editor_state") if isinstance(version.get("editor_state"), dict) else {}
    tags = template.get("tags") or []
    if not editor_state.get("imported_layout") and "import" not in tags:
        raise ValueError("Перегенерация доступна только для импортированных шаблонов")

    body_html = str(version.get("body_html") or "").strip()
    if not body_html:
        raise ValueError("Шаблон пуст")

    content_width = _detect_content_width(body_html)
    plain_text = _plain_text_from_html(body_html) or str(version.get("body_text") or "")
    preview_png = render_html_to_png(body_html, viewport_width=content_width)
    preview_pngs = [preview_png] if preview_png else []

    vision_payload, import_refinement = _refine_import_iteratively(
        filename=str(template.get("name") or "template.html"),
        plain_text=plain_text,
        draft_html=body_html,
        preview_pngs=preview_pngs,
        layout_hint=(
            "Улучши email-safe table layout, сохрани текст, плейсхолдеры и визуальную структуру письма"
        ),
        content_width=content_width,
        force_refinement=True,
    )
    if not vision_payload or not str(vision_payload.get("body_html") or "").strip():
        raise RuntimeError("AI не смог улучшить шаблон")

    new_html = _prepare_import_html(str(vision_payload["body_html"]), content_width=content_width)
    new_plain = _plain_text_from_html(new_html) or plain_text
    next_editor_state: dict[str, Any] = {
        **editor_state,
        "email_format": "visual",
        "imported_layout": True,
        "import_source": "vision_iterative",
        "import_as_draft": True,
        "import_refinement": _finalize_import_refinement(
            import_refinement,
            selected_source="vision_iterative",
            qa={"regenerated": True},
        ),
    }
    updated = template_service.save_version(
        template_id,
        owner_username,
        body_html=new_html,
        body_text=new_plain,
        editor_state=next_editor_state,
    )
    if not updated:
        raise FileNotFoundError("Шаблон не найден")
    return _externalize_data_uri_images(owner_username, updated)


def import_visual_email_template(owner_username: str, filename: str, data: bytes) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix not in _IMPORT_ALLOWED_SUFFIXES:
        formats = ", ".join(sorted(_IMPORT_ALLOWED_SUFFIXES))
        raise ValueError(f"Доступные форматы: {formats}")

    name, subject, body_html, import_source, qa_metadata = _convert_to_html(filename, data)
    plain_text = _plain_text_from_html(body_html) or template_service._file_text(filename, data)  # noqa: SLF001
    editor_state: dict[str, Any] = {
        "email_format": "visual",
        "imported_layout": True,
        "import_source": import_source,
        "import_as_draft": True,
        "import_refinement": _finalize_import_refinement(
            None,
            selected_source=import_source,
            qa=qa_metadata,
        ),
    }
    created = template_service.create_template(
        owner_username,
        name=name,
        template_type="email",
        subject=subject,
        body_html=body_html,
        body_text=plain_text,
        tags=["import"],
        editor_state=editor_state,
    )
    return _externalize_data_uri_images(owner_username, created)
