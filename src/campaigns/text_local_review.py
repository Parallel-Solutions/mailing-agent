"""Local text review rules for email templates."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.generator.generation.recipient_normalization import (
    normalize_administration_mentions,
)


@dataclass(frozen=True)
class LocalTextIssue:
    fragment: str
    message: str
    kind: str
    severity: str
    suggestion: str = ""


SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.!?;:])")
MISSING_SPACE_BEFORE_MUNICIPALITY_QUOTE_RE = re.compile(
    r'\b(муниципального\s+образования)(?P<quote>["«„“])(?=\s*[А-ЯЁа-яё])',
    re.IGNORECASE,
)
DOUBLE_COMMA_RE = re.compile(r",,")
TERRITORY_NOMINATIVE_RE = re.compile(
    r"для\s+территории\s+(?P<name>[А-ЯЁ][а-яё\-]+(?:\s+[а-яё\-]+){0,6})",
    re.IGNORECASE,
)
NOMINATIVE_SETTLEMENT_TAIL_RE = re.compile(
    r"(?:"
    r"\S+ское\s+(?:городское|сельское)\s+поселени[ея]"
    r"|(?:городское|сельское|город|село|пос[её]лок|поселение|поселения)\s+[А-ЯЁ][а-яё\-]+(?:\s+[а-яё\-]+)?"
    r")$",
    re.IGNORECASE,
)
SINGLE_BRACE_ARTIFACT_RE = re.compile(r"(?<!\{)\{(?!\{)(?!\s)[^{}<>]{2,120}")
ADMIN_NOMINATIVE_AFTER_FOR_RE = re.compile(r"\bдля\s+администрация\b", re.IGNORECASE)
NESTED_ADMINISTRATION_RE = re.compile(
    r"\bадминистраци[ия]\s+муниципального\s+образования\s+[«\"]\s*администраци[ия]\b",
    re.IGNORECASE,
)


def _normalize_plain_generated_text(text: str) -> str:
    normalized = normalize_administration_mentions(text)
    normalized = MISSING_SPACE_BEFORE_MUNICIPALITY_QUOTE_RE.sub(
        lambda match: f"{match.group(1)} {match.group('quote')}",
        normalized,
    )
    normalized = ADMIN_NOMINATIVE_AFTER_FOR_RE.sub(
        lambda match: re.sub(
            r"администрация$",
            "администрации",
            match.group(0),
            flags=re.IGNORECASE,
        ),
        normalized,
    )
    normalized = SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", normalized)
    normalized = DOUBLE_COMMA_RE.sub(",", normalized)
    normalized = re.sub(
        r"\bпредмета\s+нормирование\b",
        "предмета нормирования",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def normalize_generated_correspondence_text(text: str) -> str:
    """Apply only deterministic corrections, preserving HTML tags and attributes."""

    parts = re.split(r"(<[^>]+>)", str(text or ""))
    return "".join(
        part
        if part.startswith("<") and part.endswith(">")
        else _normalize_plain_generated_text(part)
        for part in parts
    )


def suggest_territory_genitive(name: str) -> str:
    from src.generator.inflection.inflect import inflect_mun_name_genitive

    clean = str(name or "").strip()
    if not clean:
        return "для территории"
    inflected = inflect_mun_name_genitive(clean).value
    if inflected and inflected.casefold() != clean.casefold():
        return f"для территории {inflected}"
    return f"для территории {clean}"


def _add_issue(
    issues: list[LocalTextIssue],
    *,
    fragment: str,
    message: str,
    kind: str,
    severity: str,
    suggestion: str = "",
) -> None:
    issues.append(
        LocalTextIssue(
            fragment=fragment,
            message=message,
            kind=kind,
            severity=severity,
            suggestion=suggestion,
        )
    )


def _guillemet_balance(text: str) -> int:
    return text.count("«") - text.count("»")


def review_email_text(
    text: str,
    *,
    field: str = "body",
    check_terminal_punctuation: bool = True,
) -> list[LocalTextIssue]:
    if not (text or "").strip():
        return []

    issues: list[LocalTextIssue] = []
    location = field

    for match in MISSING_SPACE_BEFORE_MUNICIPALITY_QUOTE_RE.finditer(text):
        fragment = match.group(0)
        _add_issue(
            issues,
            fragment=fragment,
            message="Перед открывающей кавычкой в названии муниципального образования пропущен пробел.",
            kind="punctuation",
            severity="warning",
            suggestion=f"{match.group(1)} {match.group('quote')}",
        )

    for match in SPACE_BEFORE_PUNCTUATION_RE.finditer(text):
        punct = match.group(1)
        fragment = match.group(0)
        _add_issue(
            issues,
            fragment=fragment,
            message=f"Пробел перед знаком препинания «{punct}».",
            kind="punctuation",
            severity="warning",
            suggestion=fragment.lstrip(),
        )

    if DOUBLE_COMMA_RE.search(text):
        _add_issue(
            issues,
            fragment=",,",
            message="Подряд идут две запятые.",
            kind="punctuation",
            severity="warning",
            suggestion=",",
        )

    balance = _guillemet_balance(text)
    if balance > 0:
        _add_issue(
            issues,
            fragment="«",
            message="Незакрытая открывающая кавычка «.",
            kind="punctuation",
            severity="warning",
        )
    elif balance < 0:
        _add_issue(
            issues,
            fragment="»",
            message="Лишняя закрывающая кавычка ».",
            kind="punctuation",
            severity="warning",
        )

    if "  " in text:
        _add_issue(
            issues,
            fragment="  ",
            message="Обнаружены двойные пробелы.",
            kind="grammar",
            severity="info",
        )

    for match in ADMIN_NOMINATIVE_AFTER_FOR_RE.finditer(text):
        fragment = match.group(0)
        _add_issue(
            issues,
            fragment=fragment,
            message="После предлога «для» название администрации должно стоять в родительном падеже.",
            kind="case",
            severity="warning",
            suggestion=re.sub(r"администрация$", "администрации", fragment, flags=re.IGNORECASE),
        )

    for match in NESTED_ADMINISTRATION_RE.finditer(text):
        normalized_text = normalize_administration_mentions(text)
        _add_issue(
            issues,
            fragment=text if normalized_text != text else match.group(0),
            message="В названии получателя повторяется слово «Администрация».",
            kind="grammar",
            severity="warning",
            suggestion=normalized_text if normalized_text != text else "",
        )
        break

    if "предмета нормирование" in text.casefold():
        _add_issue(
            issues,
            fragment="предмета нормирование",
            message="Нарушена падежная форма в словосочетании «предмета нормирование».",
            kind="case",
            severity="warning",
            suggestion="предмета нормирования",
        )

    for match in TERRITORY_NOMINATIVE_RE.finditer(text):
        name = str(match.group("name") or "").strip()
        if not name or not NOMINATIVE_SETTLEMENT_TAIL_RE.search(name):
            continue
        _add_issue(
            issues,
            fragment=match.group(0),
            message="После «для территории» вероятно нужен падеж, а не именительная форма.",
            kind="case",
            severity="warning",
            suggestion=suggest_territory_genitive(name),
        )

    for match in SINGLE_BRACE_ARTIFACT_RE.finditer(text):
        fragment = match.group(0)
        _add_issue(
            issues,
            fragment=fragment,
            message=f"В тексте остался артефакт шаблона {fragment} — замените на системную переменную, например {{WORK_TITLE}}",
            kind="artifact",
            severity="error",
        )

    if check_terminal_punctuation and text.strip() and not re.search(r"[.!?…]$", text.strip()):
        _add_issue(
            issues,
            fragment=text.strip()[-20:],
            message="Абзац не заканчивается знаком препинания.",
            kind="punctuation",
            severity="warning",
        )

    return issues
