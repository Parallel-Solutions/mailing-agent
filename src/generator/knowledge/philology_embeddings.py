from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from src.generator.generation.config_generator import (
    DATA_DIR,
    ENABLE_SEMANTIC_RAG,
    RAG_EMBEDDING_MODEL,
    RAG_SEMANTIC_MIN_SCORE,
)


SEMANTIC_INDEX_PATH = DATA_DIR / "knowledge" / "philology_semantic_index.json"
SEMANTIC_INDEX_VERSION = 1

_TOKENIZER = None
_MODEL = None
_TORCH = None
_LOAD_ERROR = ""


def semantic_search_rules(
    query: str,
    rules: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not ENABLE_SEMANTIC_RAG or not query.strip() or not rules:
        return []
    model_bundle = _load_model()
    if model_bundle is None:
        return []

    index = _load_or_build_index(rules)
    if not index:
        return []
    query_vector = _embed_text(query)
    if not query_vector:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    rules_by_key = {_rule_key(rule): rule for rule in rules}
    for item in index:
        vector = item.get("embedding")
        if not isinstance(vector, list):
            continue
        score = _cosine(query_vector, vector)
        if score < RAG_SEMANTIC_MIN_SCORE:
            continue
        rule = rules_by_key.get(str(item.get("key")))
        if not rule:
            continue
        enriched = dict(rule)
        enriched["_semantic_score"] = round(score, 4)
        enriched["_retrieval"] = "semantic"
        scored.append((score, enriched))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [rule for _, rule in scored[:limit]]


def semantic_rag_status() -> dict[str, Any]:
    return {
        "enabled": ENABLE_SEMANTIC_RAG,
        "model": RAG_EMBEDDING_MODEL,
        "index_path": str(SEMANTIC_INDEX_PATH),
        "index_exists": SEMANTIC_INDEX_PATH.exists(),
        "model_available": _load_model() is not None if ENABLE_SEMANTIC_RAG else False,
        "load_error": _LOAD_ERROR,
    }


def _load_model():
    global _TOKENIZER, _MODEL, _TORCH, _LOAD_ERROR
    if _TOKENIZER is not None and _MODEL is not None and _TORCH is not None:
        return _TOKENIZER, _MODEL, _TORCH
    try:
        import torch  # type: ignore
        from transformers import AutoModel, AutoTokenizer  # type: ignore
    except Exception as exc:
        _LOAD_ERROR = f"semantic dependencies unavailable: {exc}"
        return None
    try:
        _TOKENIZER = AutoTokenizer.from_pretrained(RAG_EMBEDDING_MODEL)
        _MODEL = AutoModel.from_pretrained(RAG_EMBEDDING_MODEL)
        _MODEL.eval()
        _TORCH = torch
    except Exception as exc:
        _LOAD_ERROR = f"model load failed: {exc}"
        _TOKENIZER = None
        _MODEL = None
        _TORCH = None
        return None
    _LOAD_ERROR = ""
    return _TOKENIZER, _MODEL, _TORCH


def _load_or_build_index(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fingerprint = _rules_fingerprint(rules)
    stored = _load_index()
    if (
        stored.get("version") == SEMANTIC_INDEX_VERSION
        and stored.get("model") == RAG_EMBEDDING_MODEL
        and stored.get("fingerprint") == fingerprint
        and isinstance(stored.get("items"), list)
    ):
        return stored["items"]

    items: list[dict[str, Any]] = []
    for rule in rules:
        text = _rule_text(rule)
        vector = _embed_text(text)
        if not vector:
            continue
        items.append(
            {
                "key": _rule_key(rule),
                "id": rule.get("id", ""),
                "embedding": vector,
            }
        )
    _save_index({"version": SEMANTIC_INDEX_VERSION, "model": RAG_EMBEDDING_MODEL, "fingerprint": fingerprint, "items": items})
    return items


def _load_index() -> dict[str, Any]:
    if not SEMANTIC_INDEX_PATH.exists():
        return {}
    try:
        payload = json.loads(SEMANTIC_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_index(payload: dict[str, Any]) -> None:
    SEMANTIC_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEMANTIC_INDEX_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _embed_text(text: str) -> list[float]:
    model_bundle = _load_model()
    if model_bundle is None:
        return []
    tokenizer, model, torch = model_bundle
    tokens = tokenizer(
        text,
        padding=True,
        truncation=True,
        max_length=256,
        return_tensors="pt",
    )
    with torch.no_grad():
        output = model(**tokens)
    embeddings = output.last_hidden_state
    mask = tokens["attention_mask"].unsqueeze(-1).expand(embeddings.size()).float()
    pooled = (embeddings * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
    vector = pooled[0].detach().cpu().tolist()
    return _normalize_vector([float(item) for item in vector])


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(item * item for item in vector))
    if norm <= 0:
        return []
    return [round(item / norm, 8) for item in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _rules_fingerprint(rules: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [{"key": _rule_key(rule), "text": _rule_text(rule)} for rule in rules],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _rule_key(rule: dict[str, Any]) -> str:
    value = str(rule.get("id") or "").strip()
    if value:
        return value
    return hashlib.sha1(_rule_text(rule).encode("utf-8")).hexdigest()[:16]


def _rule_text(rule: dict[str, Any]) -> str:
    parts = [
        rule.get("title"),
        rule.get("topic"),
        rule.get("rule"),
        " ".join(str(item) for item in (rule.get("keywords") or [])),
        " ".join(str(item) for item in (rule.get("examples") or [])),
        " ".join(str(item) for item in (rule.get("good_examples") or [])),
        " ".join(str(item) for item in (rule.get("bad_examples") or [])),
    ]
    return " ".join(str(part) for part in parts if part)
