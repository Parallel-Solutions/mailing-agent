from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import re


try:
    import pymorphy3  # type: ignore
except ImportError:  # pragma: no cover
    pymorphy3 = None


@dataclass(frozen=True)
class MunicipalityInflection:
    value: str
    changed: bool
    confidence: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


_MORPH = pymorphy3.MorphAnalyzer() if pymorphy3 else None

_CASE_GRAMMEMES = {
    "genitive": {"gent"},
    "project_genitive": {"gent"},
    "prepositional": {"loct"},
}

_INDECLINABLE_LOCALITIES = {
    "болхуны",
    "давлеканово",
    "мамедкала",
    "тлюстенхабль",
    "учалы",
    "энем",
}

_INDECLINABLE_ENDINGS = (
    "ово",
    "ево",
    "ино",
    "ы",
    "и",
)

_RURAL_LOCALITY_MARKERS = {
    "село": {"genitive": "села", "project_genitive": "села", "prepositional": "селе"},
    "деревня": {"genitive": "деревни", "project_genitive": "деревни", "prepositional": "деревне"},
    "станица": {"genitive": "станицы", "project_genitive": "станицы", "prepositional": "станице"},
    "аул": {"genitive": "аула", "project_genitive": "аула", "prepositional": "ауле"},
}

_URBAN_LOCALITY_MARKERS = {
    "город": {"genitive": "города", "project_genitive": "города", "prepositional": "городе"},
    "г": {"genitive": "города", "project_genitive": "города", "prepositional": "городе"},
    "г.": {"genitive": "города", "project_genitive": "города", "prepositional": "городе"},
    "поселок": {"genitive": "поселка", "project_genitive": "поселка", "prepositional": "поселке"},
    "посёлок": {"genitive": "поселка", "project_genitive": "поселка", "prepositional": "поселке"},
    "пгт": {"genitive": "пгт", "project_genitive": "пгт", "prepositional": "пгт"},
}

_MUNICIPALITY_PREFIXES = {
    "городское поселение": {
        "genitive": "Городского поселения",
        "project_genitive": "Городского поселения",
        "prepositional": "Городском поселении",
    },
    "сельское поселение": {
        "genitive": "Сельского поселения",
        "project_genitive": "Сельского поселения",
        "prepositional": "Сельском поселении",
    },
    "городской округ": {
        "genitive": "Городского округа",
        "project_genitive": "Городского округа",
        "prepositional": "Городском округе",
    },
    "муниципальный округ": {
        "genitive": "Муниципального округа",
        "project_genitive": "Муниципального округа",
        "prepositional": "Муниципальном округе",
    },
    "муниципальный район": {
        "genitive": "Муниципального района",
        "project_genitive": "Муниципального района",
        "prepositional": "Муниципальном районе",
    },
}


def inflect_municipality_name(name: str, target_case: str) -> MunicipalityInflection:
    normalized = _normalize_spaces(name)
    if not normalized:
        return MunicipalityInflection("", False, "empty")
    if target_case not in _CASE_GRAMMEMES:
        return MunicipalityInflection(normalized, False, "low", (f"Unsupported target case: {target_case}",))

    by_prefix = _inflect_by_prefix(normalized, target_case)
    if by_prefix:
        return by_prefix

    suffix_result = _inflect_suffix_municipality(normalized, target_case)
    if suffix_result:
        return suffix_result

    phrase = _phrase_inflect(normalized, _CASE_GRAMMEMES[target_case])
    confidence = "morph" if phrase != normalized else "low"
    warnings = () if phrase != normalized else ("Municipality was preserved by fallback morphology.",)
    return MunicipalityInflection(phrase, phrase != normalized, confidence, warnings)


def inflect_subject_rf_genitive(name: str) -> MunicipalityInflection:
    normalized = _normalize_spaces(name)
    if not normalized:
        return MunicipalityInflection("", False, "empty")
    if _starts_with_ci(normalized, "Республика "):
        tail = normalized[len("Республика ") :].strip()
        value = f"Республики {tail}"
        return MunicipalityInflection(value, value != normalized, "component_rule")
    value = _phrase_inflect(normalized, {"gent"})
    return MunicipalityInflection(value, value != normalized, "morph" if value != normalized else "low")


def inflect_municipal_district_genitive(name: str) -> MunicipalityInflection:
    normalized = _normalize_spaces(name)
    if not normalized:
        return MunicipalityInflection("", False, "empty")

    lowered = normalized.casefold()
    for suffix, replacement in (
        (" муниципальный район", "муниципального района"),
        (" муниципальный округ", "муниципального округа"),
        (" городской округ", "городского округа"),
        (" район", "района"),
    ):
        if lowered.endswith(suffix):
            head = normalized[: -len(suffix)].strip()
            head_value = _phrase_inflect(head, {"gent"}) if head else ""
            value = f"{head_value} {replacement}".strip()
            return MunicipalityInflection(value, value != normalized, "component_rule")

    for prefix, replacement in (
        ("Муниципальный район ", "муниципального района"),
        ("Муниципальный округ ", "муниципального округа"),
        ("Городской округ ", "городского округа"),
    ):
        if _starts_with_ci(normalized, prefix):
            tail = normalized[len(prefix) :].strip()
            tail_value = _phrase_inflect(tail, {"gent"})
            value = f"{tail_value} {replacement}".strip()
            return MunicipalityInflection(value, value != normalized, "component_rule")

    value = _phrase_inflect(normalized, {"gent"})
    return MunicipalityInflection(value, value != normalized, "morph" if value != normalized else "low")


def _inflect_by_prefix(normalized: str, target_case: str) -> MunicipalityInflection | None:
    lowered = normalized.casefold()
    for prefix, forms in _MUNICIPALITY_PREFIXES.items():
        if lowered == prefix:
            value = forms[target_case]
            return MunicipalityInflection(value, value != normalized, "component_rule")
        marker = prefix + " "
        if not lowered.startswith(marker):
            continue
        tail = normalized[len(marker) :].strip()
        tail_value, confidence, warnings = _inflect_municipality_tail(tail, target_case)
        value = f"{forms[target_case]} {tail_value}".strip()
        return MunicipalityInflection(value, value != normalized, confidence, warnings)
    return None


def _inflect_suffix_municipality(normalized: str, target_case: str) -> MunicipalityInflection | None:
    lowered = normalized.casefold()
    for suffix, forms in _MUNICIPALITY_PREFIXES.items():
        if not lowered.endswith(" " + suffix):
            continue
        head = normalized[: -(len(suffix) + 1)].strip()
        head_value = _phrase_inflect(head, _CASE_GRAMMEMES[target_case])
        value = f"{head_value} {forms[target_case].lower()}".strip()
        return MunicipalityInflection(value[:1].upper() + value[1:], value != normalized, "component_rule")
    return None


def _inflect_municipality_tail(tail: str, target_case: str) -> tuple[str, str, tuple[str, ...]]:
    tail = _normalize_spaces(tail)
    if not tail:
        return "", "component_rule", ()

    marker_result = _inflect_locality_marker_tail(tail, target_case)
    if marker_result:
        return marker_result

    if _looks_like_adjective(tail):
        value = _phrase_inflect(tail, _CASE_GRAMMEMES[target_case])
        return value, "component_rule", ()

    if _is_safe_to_keep_locality(tail):
        return tail, "conservative", (f"Locality kept unchanged: {tail}",)

    value = _inflect_locality_name(tail, _CASE_GRAMMEMES[target_case])
    confidence = "component_rule" if value != tail else "conservative"
    warnings = () if value != tail else (f"Locality kept unchanged: {tail}",)
    return value, confidence, warnings


def _inflect_locality_marker_tail(tail: str, target_case: str) -> tuple[str, str, tuple[str, ...]] | None:
    parts = tail.split(maxsplit=1)
    if not parts:
        return None
    marker = _normalize_marker(parts[0])
    rest = parts[1].strip() if len(parts) > 1 else ""
    marker_forms = _URBAN_LOCALITY_MARKERS.get(marker) or _RURAL_LOCALITY_MARKERS.get(marker)
    if not marker_forms:
        return None

    marker_value = marker_forms[target_case]
    if not rest:
        return marker_value, "component_rule", ()

    if marker in _RURAL_LOCALITY_MARKERS:
        locality_value = _inflect_rural_locality(rest, target_case)
    elif marker in {"поселок", "посёлок", "пгт"}:
        locality_value = rest if _is_safe_to_keep_locality(rest) else _inflect_locality_name(rest, _CASE_GRAMMEMES[target_case])
    else:
        locality_value = _inflect_city_locality(rest, target_case)

    confidence = "component_rule" if locality_value != rest or marker_value != parts[0] else "conservative"
    warnings = () if locality_value != rest or marker in {"город", "г", "г."} else (f"Locality kept unchanged: {rest}",)
    return f"{marker_value} {locality_value}".strip(), confidence, warnings


def _inflect_city_locality(name: str, target_case: str) -> str:
    normalized = _normalize_spaces(name)
    if not normalized:
        return normalized
    if _is_safe_to_keep_locality(normalized):
        return normalized
    return _inflect_locality_name(normalized, _CASE_GRAMMEMES[target_case])


def _inflect_rural_locality(name: str, target_case: str) -> str:
    normalized = _normalize_spaces(name)
    if not normalized:
        return normalized
    # Rural settlement names often preserve the official nominative form after
    # "села/деревни"; keep them unless the whole name is clearly adjectival.
    if _looks_like_adjective(normalized):
        return _phrase_inflect(normalized, _CASE_GRAMMEMES[target_case])
    return normalized


def _inflect_locality_name(name: str, grammemes: set[str]) -> str:
    normalized = _normalize_spaces(name)
    if not normalized:
        return normalized
    if _is_safe_to_keep_locality(normalized):
        return normalized
    if " " in normalized:
        return _phrase_inflect(normalized, grammemes)
    value = _simple_word_inflect(normalized, grammemes)
    return _restore_word_case(normalized, value)


def _is_safe_to_keep_locality(name: str) -> bool:
    normalized = _normalize_spaces(name)
    lower = normalized.casefold()
    if lower in _INDECLINABLE_LOCALITIES:
        return True
    if "-" in normalized:
        return True
    if lower.endswith(_INDECLINABLE_ENDINGS):
        return True
    return False


def _looks_like_adjective(value: str) -> bool:
    normalized = _normalize_spaces(value)
    if " " in normalized:
        return all(_looks_like_adjective(part) for part in normalized.split())
    lower = normalized.casefold()
    return lower.endswith(
        (
            "ский",
            "цкий",
            "нский",
            "овский",
            "евский",
            "инский",
            "ый",
            "ий",
            "ой",
            "ая",
            "яя",
            "ое",
            "ее",
        )
    )


@lru_cache(maxsize=4096)
def _simple_word_inflect_cached(word: str, grammemes_key: tuple[str, ...]) -> str:
    if not _MORPH:
        return word
    parsed = _MORPH.parse(word)
    if not parsed:
        return word
    for candidate in parsed[:3]:
        inflected = candidate.inflect(set(grammemes_key))
        if inflected:
            return inflected.word
    return word


def _simple_word_inflect(word: str, grammemes: set[str]) -> str:
    return _simple_word_inflect_cached(word, tuple(sorted(grammemes)))


def _phrase_inflect(phrase: str, grammemes: set[str]) -> str:
    normalized = _normalize_spaces(phrase)
    if not normalized or not _MORPH:
        return normalized

    words: list[str] = []
    for word in normalized.split():
        if any(char.isdigit() for char in word):
            words.append(word)
            continue
        clean = word.strip(",.()\"«»")
        if not clean:
            words.append(word)
            continue
        inflected = _simple_word_inflect(clean, grammemes)
        words.append(word.replace(clean, _restore_word_case(clean, inflected)))
    return " ".join(words)


def _restore_word_case(source_word: str, inflected_word: str) -> str:
    if source_word.isupper():
        return inflected_word.upper()
    if source_word[:1].isupper():
        return inflected_word[:1].upper() + inflected_word[1:]
    return inflected_word


def _normalize_marker(value: str) -> str:
    return value.strip().casefold().replace("ё", "е")


def _normalize_spaces(value: str) -> str:
    return " ".join(str(value or "").split())


def _starts_with_ci(value: str, prefix: str) -> bool:
    return value.casefold().startswith(prefix.casefold())
