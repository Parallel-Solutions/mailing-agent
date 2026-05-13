from __future__ import annotations

from typing import Any

from src.generator.philology_knowledge import find_relevant_rules
from src.generator.russian_linguistics import compact_linguistic_summary


def explain_fix_decision_with_rag(decision: dict[str, Any], *, limit: int = 3) -> dict[str, Any]:
    """Attach local-rule retrieval context to a philologist fix decision."""

    query = _build_query(decision)
    rules = find_relevant_rules(query, limit=limit)
    support_score = _support_score(rules)
    recommendation = _recommendation(decision, support_score)
    linguistic_summary = compact_linguistic_summary(query)
    return {
        "query": query,
        "support_score": support_score,
        "recommendation": recommendation["action"],
        "reason": recommendation["reason"],
        "linguistics": linguistic_summary,
        "rules": [_compact_rule(rule) for rule in rules],
    }


def _build_query(decision: dict[str, Any]) -> str:
    parts = [
        decision.get("issue"),
        decision.get("fragment"),
        decision.get("suggestion"),
        decision.get("reason"),
        decision.get("source"),
        decision.get("action"),
    ]
    return " ".join(_safe_text(part) for part in parts if _safe_text(part))


def _support_score(rules: list[dict[str, Any]]) -> int:
    return sum(int(rule.get("_rag_score") or 0) for rule in rules)


def _recommendation(decision: dict[str, Any], support_score: int) -> dict[str, str]:
    action = _safe_text(decision.get("action"))
    source = _safe_text(decision.get("source"))
    fragment = _safe_text(decision.get("fragment"))
    suggestion = _safe_text(decision.get("suggestion"))

    if action == "auto_fix":
        if source == "local" and support_score >= 4:
            return {
                "action": "rule_supported_auto_fix",
                "reason": "Автоправка подтверждается локальной базой правил.",
            }
        if support_score >= 10 and fragment and suggestion:
            return {
                "action": "candidate_for_safe_rule",
                "reason": "Есть сильная опора на правила; можно рассмотреть добавление безопасного правила.",
            }
        return {
            "action": "auto_fix_with_low_rag_support",
            "reason": "Автоправка технически безопасна, но опора на базу правил слабая.",
        }

    if action in {"quarantine", "needs_human"}:
        if support_score >= 10 and fragment and suggestion:
            return {
                "action": "candidate_for_human_approval",
                "reason": "RAG нашёл релевантные правила, но решение всё равно требует подтверждения.",
            }
        return {
            "action": "keep_in_quarantine",
            "reason": "Недостаточно уверенной опоры на правила для автоматизации.",
        }

    return {
        "action": "no_action",
        "reason": "Решение не требует расширения правил.",
    }


def _compact_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule.get("id", ""),
        "title": rule.get("title", ""),
        "source": rule.get("source", ""),
        "rule": rule.get("rule", ""),
        "score": rule.get("_rag_score", 0),
        "matched_terms": rule.get("_matched_terms", ""),
        "good_examples": (rule.get("good_examples") or [])[:2],
        "bad_examples": (rule.get("bad_examples") or [])[:2],
    }


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())
