"""Infer static delivery PDF filenames from document content at upload time."""

from __future__ import annotations

import re
from pathlib import Path

from src.campaigns.document_type_service import detect_document_type_key
from src.generator.generation.transforms import sanitize_path_component
from src.generator.generation.work_types import WORK_TYPE_PROFILES

_COPY_NUMBER_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
_COPY_WORD_SUFFIX_RE = re.compile(r"(?:_|\s-)копия\s*$", re.IGNORECASE)

_SCOPE_TOKENS = frozenset(
    {
        "районы",
        "район",
        "населенные",
        "пункты",
        "населенные_пункты",
        "населенные пункты",
        "mo",
        "мо",
    }
)

_GENERIC_STEM_TOKENS = frozenset(
    {
        "template",
        "document",
        "offer",
        "file",
        "шаблон",
        "документ",
        "новый",
        "new",
    }
)

_WORK_CONTENT_MARKERS: tuple[tuple[str, str], ...] = (
    ("схемы территориального планирования", "СТП"),
    ("схема территориального планирования", "СТП"),
    ("местных нормативов градостроительного проектирования", "МНГП"),
    ("местных нормативов", "МНГП"),
    ("территориальных зон", "Территориальные_зоны"),
    ("описания местоположения границ", "Территориальные_зоны"),
    ("интеллектуальной автоматизации государственного сектора", "Случайный_лес"),
)

_DOC_TYPE_PREFIX = {
    "kp": "КП",
    "contract": "Договор",
    "checklist": "Чеклист",
    "brief": "Описание",
}

_TITLE_LINE_RE = re.compile(
    r"(?:коммерческое\s+предложение|договор|чек\s*лист|краткое\s+описание)",
    re.IGNORECASE,
)

_MAX_FILENAME_LEN = 255


def _clean_upload_stem(filename: str) -> str:
    stem = Path(filename).stem.strip()
    while True:
        previous = stem
        stem = _COPY_NUMBER_SUFFIX_RE.sub("", stem).strip()
        stem = _COPY_WORD_SUFFIX_RE.sub("", stem).strip()
        if stem == previous:
            break
    return stem or "document"


def _document_type_prefix(doc_type_key: str) -> str:
    return _DOC_TYPE_PREFIX.get(doc_type_key, "")


def _profile_markers() -> list[tuple[str, str]]:
    markers: list[tuple[str, str]] = []
    for profile in WORK_TYPE_PROFILES.values():
        label = profile.filename_label
        for candidate in (
            profile.filename_label,
            profile.short_name,
            profile.label,
            profile.service_title_nominative,
            profile.result_name,
            profile.consent_prepared_phrase,
        ):
            normalized = str(candidate or "").strip()
            if normalized:
                markers.append((normalized.casefold(), label))
    markers.sort(key=lambda item: len(item[0]), reverse=True)
    return markers


def _detect_work_label(*, text: str, cleaned_stem: str) -> str | None:
    combined = f"{text}\n{cleaned_stem}".casefold()
    for marker, label in _WORK_CONTENT_MARKERS:
        if marker.casefold() in combined:
            return label

    for marker, label in _profile_markers():
        if marker in combined:
            return label

    stem_tokens = {token.casefold() for token in re.split(r"[_\s\-]+", cleaned_stem) if token}
    for profile in WORK_TYPE_PROFILES.values():
        label_token = profile.filename_label.casefold()
        if label_token in stem_tokens:
            return profile.filename_label
        short_token = profile.short_name.casefold()
        if short_token in stem_tokens:
            return profile.filename_label
    return None


def _extract_scope_suffix(cleaned_stem: str, *, prefix: str, work_label: str | None) -> str | None:
    remainder = cleaned_stem
    removed = False
    for token in (prefix, work_label or ""):
        if not token:
            continue
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        updated = pattern.sub("", remainder, count=1)
        if updated != remainder:
            removed = True
        remainder = updated
    remainder = re.sub(r"^[_\s\-]+|[_\s\-]+$", "", remainder)
    if not remainder:
        return None

    tokens = [part for part in re.split(r"[_\s\-]+", remainder) if part]
    if not tokens:
        return None

    joined = "_".join(tokens)
    normalized = joined.casefold().replace(" ", "_")
    if normalized in _SCOPE_TOKENS or any(token.casefold() in _SCOPE_TOKENS for token in tokens):
        return sanitize_path_component(joined, preserve_case=True)
    if not removed:
        return None
    if len(tokens) == 1 and len(tokens[0]) >= 3 and tokens[0].casefold() not in _GENERIC_STEM_TOKENS:
        lowered = tokens[0].casefold()
        if prefix and lowered.startswith(prefix.casefold()):
            return None
        if work_label and lowered == work_label.casefold():
            return None
        return sanitize_path_component(tokens[0], preserve_case=True)
    return None


def _extract_title_fallback(text: str) -> str | None:
    for raw_line in str(text or "").splitlines():
        line = " ".join(raw_line.split()).strip(" .")
        if len(line) < 10:
            continue
        if _TITLE_LINE_RE.search(line):
            return line
        if line.isupper() and any(char.isalpha() for char in line):
            return line
    return None


def _build_pdf_filename(base_name: str) -> str:
    stem = sanitize_path_component(base_name, preserve_case=True)
    if not stem:
        stem = "document"
    filename = f"{stem}.pdf"
    if len(filename) <= _MAX_FILENAME_LEN:
        return filename
    trim = _MAX_FILENAME_LEN - len(".pdf")
    return f"{stem[:trim]}.pdf"


def infer_static_delivery_filename(*, text: str, upload_filename: str) -> str:
    cleaned_stem = _clean_upload_stem(upload_filename)
    doc_type_key = detect_document_type_key(template_name=cleaned_stem, text=text)
    prefix = _document_type_prefix(doc_type_key)
    work_label = _detect_work_label(text=text, cleaned_stem=cleaned_stem)
    scope_suffix = _extract_scope_suffix(cleaned_stem, prefix=prefix, work_label=work_label)

    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    if work_label and work_label not in parts:
        parts.append(work_label)
    if scope_suffix and scope_suffix not in parts:
        parts.append(scope_suffix)

    if parts:
        return _build_pdf_filename("_".join(parts))

    title = _extract_title_fallback(text)
    if title:
        return _build_pdf_filename(title)

    return _build_pdf_filename(cleaned_stem)


def infer_template_display_name(delivery_filename: str) -> str:
    stem = Path(delivery_filename).stem.strip()
    if not stem:
        return "Шаблон"
    return stem.replace("_", " ")


def normalize_delivery_filename(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Имя PDF-вложения не может быть пустым")
    stem = Path(raw).stem if raw.lower().endswith(".pdf") else raw
    return _build_pdf_filename(stem)
