"""Semantic placeholder resolution using embedding search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.campaigns.placeholder_catalog import (
    PlaceholderEntry,
    catalog_entries,
    entry_search_text,
)
from src.campaigns.substitution_context import SYSTEM_AUTO_VARIABLES, SYSTEM_VARIABLE_ALIASES
from src.campaigns.substitution_engine import is_identifier_variable
from src.generator.generation.config_generator import ENABLE_SEMANTIC_RAG, RAG_SEMANTIC_MIN_SCORE
from src.generator.generation.template_analysis import _norm_token

_INDEX: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class SemanticMatch:
    canonical: str
    score: float
    kind: str


def _semantic_enabled() -> bool:
    return ENABLE_SEMANTIC_RAG


def _embed_text(text: str) -> list[float]:
    from src.generator.knowledge.philology_embeddings import _embed_text as embed

    return embed(text)


def _cosine(left: list[float], right: list[float]) -> float:
    from src.generator.knowledge.philology_embeddings import _cosine as cosine

    return cosine(left, right)


def _build_index() -> list[dict[str, Any]]:
    global _INDEX
    if _INDEX is not None:
        return _INDEX

    items: list[dict[str, Any]] = []
    for entry in catalog_entries():
        vector = _embed_text(entry_search_text(entry))
        if not vector:
            continue
        items.append(
            {
                "canonical": entry.canonical,
                "kind": entry.kind,
                "text": entry_search_text(entry),
                "embedding": vector,
            }
        )
    _INDEX = items
    return items


def reset_semantic_index() -> None:
    global _INDEX
    _INDEX = None


def _best_match(query: str, *, kind: str | None = None) -> SemanticMatch | None:
    clean = str(query or "").strip()
    if not clean or not _semantic_enabled():
        return None

    query_vector = _embed_text(clean)
    if not query_vector:
        return None

    best: SemanticMatch | None = None
    for item in _build_index():
        if kind is not None and item.get("kind") != kind:
            continue
        vector = item.get("embedding")
        if not isinstance(vector, list):
            continue
        score = _cosine(query_vector, vector)
        if score < RAG_SEMANTIC_MIN_SCORE:
            continue
        candidate = SemanticMatch(
            canonical=str(item.get("canonical") or ""),
            score=round(score, 4),
            kind=str(item.get("kind") or ""),
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def resolve_system_canonical(name: str) -> str | None:
    clean = str(name or "").strip()
    if not clean:
        return None
    if is_identifier_variable(clean):
        return "DOCUMENT_ID"

    direct = SYSTEM_VARIABLE_ALIASES.get(clean) or SYSTEM_VARIABLE_ALIASES.get(clean.lower())
    if direct:
        return direct

    normalized = _norm_token(clean)
    alias_map = {_norm_token(key): value for key, value in SYSTEM_VARIABLE_ALIASES.items()}
    if normalized in alias_map:
        return alias_map[normalized]

    for canonical in SYSTEM_AUTO_VARIABLES:
        if _norm_token(canonical) == normalized:
            return canonical

    for entry in catalog_entries(kind="system"):
        for label in (*entry.labels, *entry.aliases):
            if _norm_token(label) == normalized:
                return entry.canonical

    match = _best_match(clean, kind="system")
    if match is not None:
        return match.canonical
    return None


def resolve_recipient_canonical(name: str) -> str | None:
    clean = str(name or "").strip()
    if not clean:
        return None

    normalized = _norm_token(clean)
    for entry in catalog_entries(kind="recipient"):
        if _norm_token(entry.canonical) == normalized:
            return entry.canonical
        for label in (*entry.labels, *entry.aliases):
            if _norm_token(label) == normalized:
                return entry.canonical

    match = _best_match(clean, kind="recipient")
    if match is not None:
        return match.canonical
    return None


def semantic_match_recipient_column(
    placeholder: str,
    headers: list[str],
    *,
    samples: dict[str, list[str]] | None = None,
) -> SemanticMatch | None:
    clean = str(placeholder or "").strip()
    if not clean or not headers or not _semantic_enabled():
        return None

    canonical = resolve_recipient_canonical(clean)
    if canonical:
        normalized_canonical = _norm_token(canonical)
        for header in headers:
            if _norm_token(header) == normalized_canonical:
                return SemanticMatch(canonical=header, score=0.95, kind="recipient")

    query_vector = _embed_text(clean)
    if not query_vector:
        return None

    best: SemanticMatch | None = None
    for header in headers:
        sample_text = ""
        if samples:
            sample_values = samples.get(header) or []
            sample_text = " ".join(str(value) for value in sample_values[:2])
        header_text = " ".join(part for part in (header, sample_text) if part)
        vector = _embed_text(header_text)
        if not vector:
            continue
        score = _cosine(query_vector, vector)
        if score < RAG_SEMANTIC_MIN_SCORE:
            continue
        candidate = SemanticMatch(canonical=header, score=round(score, 4), kind="recipient")
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def apply_semantic_aliases(string_context: dict[str, str], template_text: str) -> None:
    if not template_text.strip():
        return

    from src.campaigns.substitution_engine import discover_brace_artifacts, discover_placeholders

    def _apply_alias(name: str, token: str | None = None) -> None:
        canonical = resolve_system_canonical(name) or resolve_recipient_canonical(name)
        if not canonical:
            return
        value = string_context.get(canonical) or string_context.get(canonical.upper())
        if not value:
            return
        if name not in string_context:
            string_context[name] = value
        if token and token not in string_context:
            string_context[token] = value

    for item in discover_placeholders(template_text):
        _apply_alias(item.name, item.token)

    for item in discover_brace_artifacts(template_text):
        _apply_alias(item.name, item.token)
