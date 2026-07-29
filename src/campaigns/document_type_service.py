"""Detect document type keys from template content and names."""

from __future__ import annotations

import re

from src.generator.generation.template_analysis import _norm_token

_CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

_KP_MARKERS = (
    "коммерческоепредложение",
    "коммерческое предложение",
)
_CONTRACT_MARKERS = ("договор",)
_CHECKLIST_MARKERS = ("чеклист", "checklist")
_BRIEF_MARKERS = ("краткоеописание", "краткое описание")


def _slugify_name(name: str) -> str:
    chunks: list[str] = []
    for char in str(name or "").strip().lower():
        if char in _CYRILLIC_TO_LATIN:
            chunks.append(_CYRILLIC_TO_LATIN[char])
        elif char.isascii() and char.isalnum():
            chunks.append(char)
        elif char in {" ", "-", "_", "."}:
            chunks.append("-")
    slug = re.sub(r"-+", "-", "".join(chunks)).strip("-")
    return slug or "document"


def _match_content_type(normalized_text: str, raw_text: str) -> str | None:
    folded = str(raw_text or "").casefold()
    if any(marker in normalized_text or marker in folded for marker in _KP_MARKERS):
        return "kp"
    if "-кп" in folded or re.search(r"(?<![a-zа-яё])кп(?![a-zа-яё])", folded):
        return "kp"
    if any(marker in normalized_text or marker in folded for marker in _CONTRACT_MARKERS):
        return "contract"
    if any(marker.replace(" ", "") in normalized_text or marker in folded for marker in _CHECKLIST_MARKERS):
        return "checklist"
    if any(marker.replace(" ", "") in normalized_text or marker in folded for marker in _BRIEF_MARKERS):
        return "brief"
    return None


def detect_document_type_key(*, template_name: str, text: str) -> str:
    combined = "\n".join(part for part in (text, template_name) if part).strip()
    normalized = _norm_token(combined)
    from_content = _match_content_type(normalized, combined)
    if from_content:
        return from_content

    normalized_name = _norm_token(template_name)
    from_name = _match_content_type(normalized_name, template_name)
    if from_name:
        return from_name

    return _slugify_name(template_name)
