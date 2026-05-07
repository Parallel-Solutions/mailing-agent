from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.generator.config_generator import DATA_DIR


PHILOLOGY_RULES_PATH = DATA_DIR / "knowledge" / "philology_rules.json"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def load_philology_rules() -> list[dict[str, Any]]:
    if not PHILOLOGY_RULES_PATH.exists():
        return []
    try:
        payload = json.loads(PHILOLOGY_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [item for item in payload if isinstance(item, dict)]


def find_relevant_rules(text: str, *, limit: int = 4) -> list[dict[str, Any]]:
    query = _safe_text(text).lower()
    all_rules = load_philology_rules()
    if not query:
        return all_rules[:limit]

    scored: list[tuple[int, dict[str, Any]]] = []
    for rule in all_rules:
        score = 0
        for keyword in rule.get("keywords", []) or []:
            normalized = _safe_text(keyword).lower()
            if normalized and normalized in query:
                score += 2
        rule_text = " ".join(
            [
                _safe_text(rule.get("title")),
                _safe_text(rule.get("topic")),
                _safe_text(rule.get("rule")),
                " ".join(_safe_text(x) for x in (rule.get("examples") or [])),
            ]
        ).lower()
        for token in {token for token in query.split() if len(token) >= 5}:
            if token in rule_text:
                score += 1
        if score > 0:
            scored.append((score, rule))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [rule for _, rule in scored[:limit]]
    if selected:
        return selected
    return all_rules[:limit]


def format_rules_context(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return "Подходящие правила из локальной базы не найдены."
    chunks: list[str] = []
    for rule in rules:
        examples = "; ".join(_safe_text(item) for item in (rule.get("examples") or [])[:3])
        chunks.append(
            f"[{_safe_text(rule.get('id'))}] {_safe_text(rule.get('title'))}\n"
            f"Источник: {_safe_text(rule.get('source'))}\n"
            f"Правило: {_safe_text(rule.get('rule'))}\n"
            f"Примеры: {examples}"
        )
    return "\n\n".join(chunks)
