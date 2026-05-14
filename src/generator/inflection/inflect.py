from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from src.generator.inflection.municipality_inflector import (
    inflect_municipal_district_genitive as component_inflect_municipal_district_genitive,
)
from src.generator.inflection.municipality_inflector import (
    inflect_municipality_name as component_inflect_municipality_name,
)
from src.generator.inflection.municipality_inflector import (
    inflect_subject_rf_genitive as component_inflect_subject_rf_genitive,
)


try:
    import pymorphy3  # type: ignore
except ImportError:  # pragma: no cover
    pymorphy3 = None


@dataclass
class InflectionResult:
    value: str
    changed: bool
    confidence: str
    warnings: tuple[str, ...] = ()


_MORPH = pymorphy3.MorphAnalyzer() if pymorphy3 else None
_INDECLINABLE_LOCALITIES = {
    "энем",
}


def _normalize_spaces(value: str) -> str:
    return " ".join(str(value).split())


def _safe_title_if_upper(value: str) -> str:
    value = _normalize_spaces(value)
    if value.isupper():
        return value.title()
    return value


def _safe_sentence_case_if_upper(value: str) -> str:
    value = _normalize_spaces(value)
    if not value or not value.isupper():
        return value

    result = value.lower()
    chars = list(result)
    capitalize_next = True

    for index, char in enumerate(chars):
        if capitalize_next and char.isalpha():
            chars[index] = char.upper()
            capitalize_next = False
            continue
        if char in {'"', '«', '('}:
            capitalize_next = True

    return "".join(chars)


def _replace_case_insensitive(text: str, target: str, replacement: str) -> str:
    if not target:
        return text
    pattern = re.compile(re.escape(target), re.IGNORECASE)
    return pattern.sub(replacement, text)


@lru_cache(maxsize=4096)
def _simple_word_inflect_cached(word: str, grammemes_key: tuple[str, ...]) -> str:
    if not _MORPH:
        return word

    parsed = _MORPH.parse(word)
    if not parsed:
        return word

    best = parsed[0]
    inflected = best.inflect(set(grammemes_key))
    if not inflected:
        return word
    return inflected.word


def _simple_word_inflect(word: str, grammemes: set[str]) -> str:
    return _simple_word_inflect_cached(word, tuple(sorted(grammemes)))


def _to_nominative_single_word(word: str) -> str:
    normalized = _normalize_spaces(word)
    if not normalized or " " in normalized or not _MORPH:
        return normalized

    parsed = _MORPH.parse(normalized)
    if not parsed:
        return normalized

    for candidate in parsed:
        inflected = candidate.inflect({"nomn"})
        if inflected:
            return _restore_word_case(normalized, inflected.word)
    return normalized


def _maybe_inflect_single_settlement_name(name: str, grammemes: set[str]) -> str:
    normalized = _normalize_spaces(name)
    if " " in normalized or not normalized:
        return normalized
    lower = normalized.lower()
    adjective_like_endings = (
        "ский",
        "цкий",
        "нский",
        "овский",
        "евский",
        "инский",
        "ый",
        "ий",
        "ой",
        "ое",
        "ее",
    )
    if not lower.endswith(adjective_like_endings):
        return normalized
    inflected = _simple_word_inflect(normalized, grammemes)
    return _restore_word_case(normalized, inflected)


def _inflect_locality_name(name: str, grammemes: set[str]) -> str:
    normalized = _normalize_spaces(name)
    if not normalized:
        return normalized
    if normalized.lower() in _INDECLINABLE_LOCALITIES:
        return normalized
    if " " in normalized:
        return _phrase_inflect(normalized, grammemes).value
    # For official municipality names we should be conservative with single-word
    # localities: "Давлеканово", "Учалы", "Белебей" are often better preserved
    # as-is than mechanically inflected into awkward document forms.
    inflected = _maybe_inflect_single_settlement_name(normalized, grammemes)
    if inflected != normalized:
        return inflected
    return normalized


def _normalize_city_or_settlement_locality(name: str) -> str:
    normalized = _normalize_spaces(name)
    if not normalized:
        return normalized
    if " " in normalized:
        return normalized
    return _to_nominative_single_word(normalized)


def _capitalize_phrase_words(value: str) -> str:
    words = []
    for word in _normalize_spaces(value).split():
        parts = [part[:1].upper() + part[1:] if part else part for part in word.split("-")]
        words.append("-".join(parts))
    return " ".join(words)


def _normalize_mo_tail_case(value: str) -> str:
    text = _capitalize_phrase_words(value)
    return re.sub(r"\b(Сельсовет|Поссовет)\b", lambda match: match.group(1).lower(), text)


def _restore_word_case(source_word: str, inflected_word: str) -> str:
    if source_word.isupper():
        return inflected_word.upper()
    if source_word[:1].isupper():
        return inflected_word[:1].upper() + inflected_word[1:]
    return inflected_word


def _phrase_inflect(phrase: str, grammemes: set[str]) -> InflectionResult:
    phrase = _normalize_spaces(phrase)
    if not phrase:
        return InflectionResult(value="", changed=False, confidence="empty")

    if not _MORPH:
        return InflectionResult(value=phrase, changed=False, confidence="no_morph")

    original = phrase
    words = phrase.split()
    inflected_words: list[str] = []

    for word in words:
        if any(symbol.isdigit() for symbol in word):
            inflected_words.append(word)
            continue

        clean_word = word.strip(",.()\"")
        if not clean_word:
            inflected_words.append(word)
            continue

        inflected = _simple_word_inflect(clean_word, grammemes)
        inflected = _restore_word_case(clean_word, inflected)
        inflected_words.append(word.replace(clean_word, inflected))

    result = " ".join(inflected_words)
    return InflectionResult(
        value=result,
        changed=result != original,
        confidence="auto",
    )


def _infer_fio_gender(parts: list[str]) -> str:
    if len(parts) >= 3:
        patronymic = parts[2].lower()
        if patronymic.endswith(("вна", "ична", "инична", "овна", "евна")):
            return "femn"
        if patronymic.endswith(("ич", "оглы")):
            return "masc"
    if len(parts) >= 2:
        name = parts[1].lower()
        if name.endswith(("а", "я")):
            return "femn"
    return "masc"


def _inflect_fio_surname(word: str, target_case: str, gender: str) -> str:
    lower = word.lower()
    if gender == "femn":
        if target_case == "gent":
            if lower.endswith(("ова", "ева", "ина", "ына")):
                return word[:-1] + "ой"
            if lower.endswith("ая"):
                return word[:-2] + "ой"
            if lower.endswith("яя"):
                return word[:-2] + "ей"
        if target_case == "datv":
            if lower.endswith(("ова", "ева", "ина", "ына")):
                return word[:-1] + "ой"
            if lower.endswith("ая"):
                return word[:-2] + "ой"
            if lower.endswith("яя"):
                return word[:-2] + "ей"
        if lower.endswith("ь"):
            return word
        if _looks_like_feminine_indeclinable_name_part(word):
            return word
    else:
        if target_case == "gent":
            if lower.endswith(("ов", "ев", "ин", "ын")):
                return word + "а"
            if lower.endswith("ский"):
                return word[:-2] + "ого"
            if lower.endswith("цкий"):
                return word[:-2] + "ого"
        if target_case == "datv":
            if lower.endswith(("ов", "ев", "ин", "ын")):
                return word + "у"
            if lower.endswith("ский"):
                return word[:-2] + "ому"
            if lower.endswith("цкий"):
                return word[:-2] + "ому"
    return _restore_word_case(word, _simple_word_inflect(word, {target_case}))


def _looks_like_feminine_indeclinable_name_part(word: str) -> bool:
    lower = word.casefold()
    if not lower:
        return False
    if lower.endswith(("а", "я", "ь")):
        return False
    return bool(re.search(r"[бвгджзклмнпрстфхцчшщ]$", lower))


def _inflect_masculine_name_by_rule(word: str, target_case: str) -> str | None:
    lower = word.casefold()
    if not lower:
        return None
    if lower.endswith(("а", "я", "о", "е", "ё", "у", "ю", "ы", "и")):
        return None
    if target_case == "gent":
        if lower.endswith(("й", "ь")):
            return word[:-1] + "я"
        return word + "а"
    if target_case == "datv":
        if lower.endswith(("й", "ь")):
            return word[:-1] + "ю"
        return word + "у"
    return None


def _inflect_fio_name_or_patronymic(word: str, target_case: str, gender: str, role: str) -> str:
    if gender == "femn" and role == "name" and _looks_like_feminine_indeclinable_name_part(word):
        return word
    if gender == "masc" and role == "name":
        inflected = _inflect_masculine_name_by_rule(word, target_case)
        if inflected:
            return inflected
    return _restore_word_case(word, _simple_word_inflect(word, {target_case}))


def _fio_inflect(fio: str, target_case: str) -> InflectionResult:
    fio = _safe_title_if_upper(fio)
    parts = [part for part in _normalize_spaces(fio).split() if part]
    if not parts:
        return InflectionResult(value="", changed=False, confidence="empty")
    if len(parts) != 3:
        return _phrase_inflect(fio, {target_case})

    gender = _infer_fio_gender(parts)
    surname, name, patronymic = parts
    inflected_parts = [
        _inflect_fio_surname(surname, target_case, gender),
        _inflect_fio_name_or_patronymic(name, target_case, gender, "name"),
        _inflect_fio_name_or_patronymic(patronymic, target_case, gender, "patronymic"),
    ]
    result = " ".join(inflected_parts)
    return InflectionResult(
        value=result,
        changed=result != fio,
        confidence="rule",
    )


def inflect_fio_genitive(fio: str) -> InflectionResult:
    return _fio_inflect(fio, "gent")


def inflect_fio_dative(fio: str) -> InflectionResult:
    return _fio_inflect(fio, "datv")


def inflect_mun_name_genitive(name: str) -> InflectionResult:
    result = component_inflect_municipality_name(name, "genitive")
    return InflectionResult(
        value=result.value,
        changed=result.changed,
        confidence="rule" if result.confidence in {"component_rule", "conservative"} else result.confidence,
        warnings=result.warnings,
    )


def inflect_mun_name_dative(name: str) -> InflectionResult:
    return _phrase_inflect(name, {"datv"})


def inflect_mun_name_prepositional(name: str) -> InflectionResult:
    result = component_inflect_municipality_name(name, "prepositional")
    return InflectionResult(
        value=result.value,
        changed=result.changed,
        confidence="rule" if result.confidence in {"component_rule", "conservative"} else result.confidence,
        warnings=result.warnings,
    )


def inflect_sub_rf_genitive(name: str) -> InflectionResult:
    result = component_inflect_subject_rf_genitive(name)
    return InflectionResult(
        value=result.value,
        changed=result.changed,
        confidence="rule" if result.confidence == "component_rule" else result.confidence,
        warnings=result.warnings,
    )


def inflect_mun_r_name_genitive(name: str) -> InflectionResult:
    result = component_inflect_municipal_district_genitive(name)
    return InflectionResult(
        value=result.value,
        changed=result.changed,
        confidence="rule" if result.confidence == "component_rule" else result.confidence,
        warnings=result.warnings,
    )


def inflect_mun_name_project_form(name: str) -> InflectionResult:
    # In the project-description phrase we need "нормативов ... чего?",
    # so the municipality name should stay in the genitive form there.
    return inflect_mun_name_genitive(name)


def inflect_admin_name_genitive(name: str) -> InflectionResult:
    name = _safe_sentence_case_if_upper(name)
    if not name:
        return InflectionResult(value="", changed=False, confidence="empty")

    prefix = "Администрация муниципального образования "
    if name.startswith(prefix):
        prefix_result = _phrase_inflect(prefix.strip(), {"gent"})
        suffix = name[len(prefix) :]
        value = f"{prefix_result.value} {suffix}".strip()
        return InflectionResult(
            value=value,
            changed=value != name,
            confidence="rule",
        )

    return _phrase_inflect(name, {"gent"})


def build_inflected_fields(row: dict) -> dict:
    fio = str(row.get("HEAD_FIO") or "")
    mun_name = str(row.get("MUN_NAME") or "")
    sub_rf = str(row.get("SUB_RF") or "")
    adm_name = _safe_sentence_case_if_upper(str(row.get("ADM_NAME") or ""))
    mun_r_name = str(row.get("MUN_R_NAME") or "")

    fio_gen = inflect_fio_genitive(fio)
    fio_dat = inflect_fio_dative(fio)
    mun_gen = inflect_mun_name_genitive(mun_name)
    mun_project = inflect_mun_name_project_form(mun_name)
    mun_prep = inflect_mun_name_prepositional(mun_name)
    sub_rf_gen = inflect_sub_rf_genitive(sub_rf)
    adm_gen = inflect_admin_name_genitive(adm_name)
    mun_r_gen = inflect_mun_r_name_genitive(mun_r_name)

    # Preserve proper-name capitalization inside the fully-inflected administration title.
    adm_gen_value = adm_gen.value
    adm_gen_value = _replace_case_insensitive(adm_gen_value, mun_gen.value.lower(), mun_gen.value)
    adm_gen_value = _replace_case_insensitive(adm_gen_value, mun_r_gen.value.lower(), mun_r_gen.value)
    adm_gen_value = _replace_case_insensitive(adm_gen_value, sub_rf_gen.value.lower(), sub_rf_gen.value)
    if sub_rf.startswith("Республика "):
        adm_gen_value = re.sub(r"республики\s+\S+", sub_rf_gen.value, adm_gen_value, flags=re.IGNORECASE)
    if adm_name.count('"') > adm_gen_value.count('"'):
        adm_gen_value += '"'

    return {
        "HEAD_FIO_1": fio_gen.value,
        "HEAD_FIO_2": fio_dat.value,
        "MUN_NAME_1": mun_gen.value,
        "MUN_NAME_2": mun_project.value,
        "MUN_NAME_3": mun_prep.value,
        "SUB_RF_1": sub_rf_gen.value,
        "ADM_NAME_1": adm_gen_value,
        "MUN_R_NAME_1": mun_r_gen.value,
        "INFLECTION_DEBUG": {
            "HEAD_FIO_1": fio_gen.confidence,
            "HEAD_FIO_2": fio_dat.confidence,
            "MUN_NAME_1": mun_gen.confidence,
            "MUN_NAME_2": mun_project.confidence,
            "MUN_NAME_3": mun_prep.confidence,
            "SUB_RF_1": sub_rf_gen.confidence,
            "ADM_NAME_1": adm_gen.confidence,
            "MUN_R_NAME_1": mun_r_gen.confidence,
        },
    }
