"""Local text review rules for email templates."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalTextIssue:
    fragment: str
    message: str
    kind: str
    severity: str
    suggestion: str = ""


SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.!?;:])")
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


def review_email_text(text: str, *, field: str = "body") -> list[LocalTextIssue]:
    if not (text or "").strip():
        return []

    issues: list[LocalTextIssue] = []
    location = field

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

    if text.strip() and not re.search(r"[.!?…]$", text.strip()):
        _add_issue(
            issues,
            fragment=text.strip()[-20:],
            message="Абзац не заканчивается знаком препинания.",
            kind="punctuation",
            severity="warning",
        )

    return issues
