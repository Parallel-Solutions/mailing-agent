from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.generator.config_generator import DATA_DIR
from src.generator.config_generator import RAG_SEMANTIC_WEIGHT
from src.generator.philology_embeddings import semantic_search_rules
from src.generator.philology_sources import source_chunks_as_rules


PHILOLOGY_RULES_PATH = DATA_DIR / "knowledge" / "philology_rules.json"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _normalize_for_search(value: Any) -> str:
    text = _safe_text(value).casefold().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    return {token for token in _normalize_for_search(value).split() if len(token) >= 4}


def load_philology_rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    if not PHILOLOGY_RULES_PATH.exists():
        return source_chunks_as_rules()
    try:
        payload = json.loads(PHILOLOGY_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = []
    rules.extend(item for item in payload if isinstance(item, dict))
    rules.extend(source_chunks_as_rules())
    return rules


def find_relevant_rules(text: str, *, limit: int = 4) -> list[dict[str, Any]]:
    query = _normalize_for_search(text)
    query_tokens = _tokens(text)
    all_rules = load_philology_rules()
    if not query:
        return all_rules[:limit]

    scored_by_key: dict[str, dict[str, Any]] = {}
    for rule in all_rules:
        score = 0
        matched_terms: list[str] = []
        for keyword in rule.get("keywords", []) or []:
            normalized = _normalize_for_search(keyword)
            if normalized and normalized in query:
                score += 6
                matched_terms.append(_safe_text(keyword))

        topic = _normalize_for_search(rule.get("topic"))
        if topic and topic in query:
            score += 4
            matched_terms.append(_safe_text(rule.get("topic")))

        title_tokens = _tokens(rule.get("title"))
        topic_tokens = _tokens(rule.get("topic"))
        example_tokens = set()
        for key in ("examples", "good_examples", "bad_examples"):
            for item in rule.get(key, []) or []:
                example_tokens.update(_tokens(item))

        score += len(query_tokens & title_tokens) * 3
        score += len(query_tokens & topic_tokens) * 3
        score += len(query_tokens & example_tokens) * 2

        rule_text = " ".join(
            [
                _safe_text(rule.get("title")),
                _safe_text(rule.get("topic")),
                _safe_text(rule.get("rule")),
                " ".join(_safe_text(x) for x in (rule.get("examples") or [])),
                " ".join(_safe_text(x) for x in (rule.get("good_examples") or [])),
                " ".join(_safe_text(x) for x in (rule.get("bad_examples") or [])),
            ]
        )
        normalized_rule_text = _normalize_for_search(rule_text)
        for token in query_tokens:
            if token in normalized_rule_text:
                score += 1
                if len(matched_terms) < 5:
                    matched_terms.append(token)
        if score > 0:
            key = _rule_key(rule)
            scored_by_key[key] = {
                "score": score,
                "matched": ", ".join(dict.fromkeys(matched_terms)),
                "rule": rule,
                "semantic_score": 0.0,
                "retrieval": "keyword",
            }

    for semantic_rule in semantic_search_rules(text, all_rules, limit=max(limit * 2, 8)):
        key = _rule_key(semantic_rule)
        semantic_score = float(semantic_rule.get("_semantic_score") or 0.0)
        semantic_points = int(round(semantic_score * RAG_SEMANTIC_WEIGHT))
        existing = scored_by_key.get(key)
        if existing:
            existing["score"] += semantic_points
            existing["semantic_score"] = semantic_score
            existing["retrieval"] = "hybrid"
            existing["rule"] = semantic_rule
            continue
        scored_by_key[key] = {
            "score": semantic_points,
            "matched": semantic_rule.get("_matched_terms", ""),
            "rule": semantic_rule,
            "semantic_score": semantic_score,
            "retrieval": "semantic",
        }

    scored = sorted(scored_by_key.values(), key=lambda item: int(item["score"]), reverse=True)
    selected = []
    for item in scored[:limit]:
        score = int(item["score"])
        matched = str(item["matched"] or "")
        rule = item["rule"]
        enriched = dict(rule)
        enriched["_rag_score"] = score
        enriched["_matched_terms"] = matched
        enriched["_keyword_score"] = score - int(round(float(item.get("semantic_score") or 0.0) * RAG_SEMANTIC_WEIGHT))
        enriched["_semantic_score"] = item.get("semantic_score", 0.0)
        enriched["_retrieval"] = item.get("retrieval", "keyword")
        selected.append(enriched)
    if selected:
        return selected
    return all_rules[:limit]


def format_rules_context(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return "Подходящие правила из локальной базы не найдены."
    chunks: list[str] = []
    for rule in rules:
        examples = "; ".join(_safe_text(item) for item in (rule.get("examples") or [])[:3])
        good_examples = "; ".join(_safe_text(item) for item in (rule.get("good_examples") or [])[:3])
        bad_examples = "; ".join(_safe_text(item) for item in (rule.get("bad_examples") or [])[:3])
        matched = _safe_text(rule.get("_matched_terms"))
        score = rule.get("_rag_score")
        meta = f"score={score}" if score is not None else ""
        retrieval = _safe_text(rule.get("_retrieval"))
        semantic_score = rule.get("_semantic_score")
        if matched:
            meta = f"{meta}; matched={matched}".strip("; ")
        if retrieval:
            meta = f"{meta}; retrieval={retrieval}".strip("; ")
        if semantic_score:
            meta = f"{meta}; semantic={semantic_score}".strip("; ")
        chunks.append(
            f"[{_safe_text(rule.get('id'))}] {_safe_text(rule.get('title'))}\n"
            f"Источник: {_safe_text(rule.get('source'))}\n"
            f"Правило: {_safe_text(rule.get('rule'))}\n"
            f"Примеры: {examples}\n"
            f"Правильно: {good_examples}\n"
            f"Неправильно: {bad_examples}\n"
            f"Поиск: {meta}"
        )
    return "\n\n".join(chunks)


def _rule_key(rule: dict[str, Any]) -> str:
    value = _safe_text(rule.get("id"))
    if value:
        return value
    return _safe_text(rule.get("title")) + "|" + _safe_text(rule.get("source"))
