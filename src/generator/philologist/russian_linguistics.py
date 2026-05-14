from __future__ import annotations

import re
from functools import lru_cache
from typing import Any


OFFICIAL_NAME_PATTERNS = (
    re.compile(
        r"\b(?P<kind>Городск\w+\s+поселени\w+|Сельск\w+\s+поселени\w+|муниципальн\w+\s+образовани\w+)"
        r"(?:\s+(?P<object>город\w+|села|пос[её]лка|деревн\w+))?"
        r"\s+(?P<name>[А-ЯЁ][А-ЯЁа-яё\-\s]{2,80})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<district>[А-ЯЁ][А-ЯЁа-яё\-]+ск\w+)\s+муниципальн\w+\s+район\w+",
        re.IGNORECASE,
    ),
    re.compile(r"\bРеспублик\w+\s+(?P<republic>[А-ЯЁ][А-ЯЁа-яё\-]+)", re.IGNORECASE),
)


def linguistic_tools_status() -> dict[str, Any]:
    return {
        "pymorphy3": _module_available("pymorphy3"),
        "natasha": _module_available("natasha"),
        "yargy": _module_available("yargy"),
        "mode": "natasha+pymorphy3" if _module_available("natasha") else "regex+pymorphy3",
    }


def analyze_russian_text(text: str, *, token_limit: int = 40) -> dict[str, Any]:
    text = _safe_text(text)
    return {
        "status": linguistic_tools_status(),
        "entities": _extract_natasha_entities(text),
        "official_names": extract_official_name_fragments(text),
        "tokens": _analyze_tokens(text, limit=token_limit),
    }


def compact_linguistic_summary(text: str) -> dict[str, Any]:
    analysis = analyze_russian_text(text, token_limit=20)
    return {
        "mode": analysis["status"]["mode"],
        "entity_count": len(analysis.get("entities") or []),
        "official_name_count": len(analysis.get("official_names") or []),
        "official_names": analysis.get("official_names", [])[:5],
        "important_tokens": [
            token
            for token in analysis.get("tokens", [])
            if token.get("pos") in {"NOUN", "ADJF", "Name", "Surn", "Geox", "Orgn"}
        ][:10],
    }


def extract_official_name_fragments(text: str) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for pattern in OFFICIAL_NAME_PATTERNS:
        for match in pattern.finditer(text):
            fragment = _safe_text(match.group(0))
            if not fragment:
                continue
            fragments.append(
                {
                    "fragment": fragment,
                    "start": match.start(),
                    "end": match.end(),
                    "groups": {key: _safe_text(value) for key, value in match.groupdict().items() if value},
                }
            )
    return _dedupe_fragments(fragments)


def _extract_natasha_entities(text: str) -> list[dict[str, Any]]:
    bundle = _load_natasha()
    if bundle is None:
        return []
    Doc, segmenter, emb, ner_tagger = bundle
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner_tagger)
    entities = []
    for span in doc.spans:
        entities.append(
            {
                "text": span.text,
                "type": span.type,
                "start": span.start,
                "end": span.stop,
            }
        )
    return entities


def _analyze_tokens(text: str, *, limit: int) -> list[dict[str, Any]]:
    morph = _load_morph()
    words = re.findall(r"[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]{1,}", text)
    tokens = []
    for word in words[:limit]:
        if morph is None:
            tokens.append({"text": word, "normal_form": word.casefold(), "pos": "", "case": "", "gender": "", "number": ""})
            continue
        parsed = morph.parse(word)[0]
        tokens.append(
            {
                "text": word,
                "normal_form": parsed.normal_form,
                "pos": parsed.tag.POS or "",
                "case": parsed.tag.case or "",
                "gender": parsed.tag.gender or "",
                "number": parsed.tag.number or "",
                "score": round(float(parsed.score), 4),
            }
        )
    return tokens


@lru_cache(maxsize=1)
def _load_morph():
    try:
        import pymorphy3  # type: ignore
    except Exception:
        return None
    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=1)
def _load_natasha():
    try:
        from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter  # type: ignore
    except Exception:
        return None
    segmenter = Segmenter()
    emb = NewsEmbedding()
    ner_tagger = NewsNERTagger(emb)
    return Doc, segmenter, emb, ner_tagger


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return False
    return True


def _dedupe_fragments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = (item.get("fragment"), item.get("start"), item.get("end"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())
