from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


AUTO_FIX = "auto_fix"
QUARANTINE = "quarantine"
NEEDS_HUMAN = "needs_human"
SKIP = "skip"


EDITORIAL_SUGGESTION_PREFIXES = (
    "заменить ",
    "исправить ",
    "нужно ",
    "следует ",
    "проверить ",
    "убрать ",
)


@dataclass(frozen=True)
class PhilologistFixDecision:
    action: str
    reason: str
    confidence: float
    source: str
    issue: str
    location: str
    fragment: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_issue_fix(issue: dict[str, Any], *, current_text: str = "") -> PhilologistFixDecision:
    source = _safe_text(issue.get("source")) or "unknown"
    issue_text = _safe_text(issue.get("issue"))
    issue_lower = issue_text.lower()
    location = _safe_text(issue.get("location"))
    fragment = _safe_text(issue.get("fragment"))
    suggestion = _safe_text(issue.get("suggestion"))
    current_text = _safe_text(current_text)

    if "двойные пробелы" in issue_lower:
        return PhilologistFixDecision(
            action=AUTO_FIX,
            reason="Механическая нормализация пробелов безопасна для автоправки.",
            confidence=0.98,
            source=source,
            issue=issue_text,
            location=location,
            fragment=fragment,
            suggestion=suggestion,
        )

    if not suggestion:
        return PhilologistFixDecision(
            action=SKIP,
            reason="Нет предложенного исправления.",
            confidence=0.95,
            source=source,
            issue=issue_text,
            location=location,
            fragment=fragment,
            suggestion=suggestion,
        )

    if _looks_like_editorial_instruction(suggestion):
        return PhilologistFixDecision(
            action=QUARANTINE,
            reason="Предложение выглядит как редакторская инструкция, а не готовая замена.",
            confidence=0.92,
            source=source,
            issue=issue_text,
            location=location,
            fragment=fragment,
            suggestion=suggestion,
        )

    if source == "ai" and not fragment:
        return PhilologistFixDecision(
            action=QUARANTINE,
            reason="AI предлагает правку без точного фрагмента, это может переписать абзац и сломать стиль.",
            confidence=0.9,
            source=source,
            issue=issue_text,
            location=location,
            fragment=fragment,
            suggestion=suggestion,
        )

    if fragment and current_text and fragment not in current_text and current_text.strip() != fragment.strip():
        return PhilologistFixDecision(
            action=SKIP,
            reason="Фрагмент для замены не найден в текущем тексте.",
            confidence=0.9,
            source=source,
            issue=issue_text,
            location=location,
            fragment=fragment,
            suggestion=suggestion,
        )

    if source == "ai" and _replacement_is_too_large(fragment, suggestion):
        return PhilologistFixDecision(
            action=NEEDS_HUMAN,
            reason="AI предлагает слишком крупную замену, её должен подтвердить человек.",
            confidence=0.86,
            source=source,
            issue=issue_text,
            location=location,
            fragment=fragment,
            suggestion=suggestion,
        )

    if source == "local":
        return PhilologistFixDecision(
            action=AUTO_FIX,
            reason="Локальное правило дало точечную замену.",
            confidence=0.94,
            source=source,
            issue=issue_text,
            location=location,
            fragment=fragment,
            suggestion=suggestion,
        )

    if source == "ai" and fragment:
        return PhilologistFixDecision(
            action=AUTO_FIX,
            reason="AI-правка разрешена только как точечная замена найденного фрагмента.",
            confidence=0.74,
            source=source,
            issue=issue_text,
            location=location,
            fragment=fragment,
            suggestion=suggestion,
        )

    return PhilologistFixDecision(
        action=NEEDS_HUMAN,
        reason="Правка не попала в безопасные автоматические сценарии.",
        confidence=0.7,
        source=source,
        issue=issue_text,
        location=location,
        fragment=fragment,
        suggestion=suggestion,
    )


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _looks_like_editorial_instruction(text: str) -> bool:
    normalized = _safe_text(text).strip().lower()
    if not normalized:
        return False
    if any(normalized.startswith(prefix) for prefix in EDITORIAL_SUGGESTION_PREFIXES):
        return True
    return "заменить" in normalized and (" на " in normalized or '"' in normalized or "«" in normalized)


def _replacement_is_too_large(fragment: str, suggestion: str) -> bool:
    fragment = _safe_text(fragment)
    suggestion = _safe_text(suggestion)
    if not fragment or not suggestion:
        return False
    if len(suggestion) > max(120, len(fragment) * 2):
        return True
    return suggestion.count(".") > fragment.count(".") + 1
