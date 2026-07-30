from datetime import datetime
import re
from typing import Optional

from src.generator.case_engine import build_inflected_fields_with_trace
from src.generator.generation.recipient_normalization import (
    contains_nested_administration,
    extract_administration_entity_name,
    is_district_level_entity_name,
    normalize_administration_recipient,
)
from src.generator.generation.work_types import build_work_type_context, normalize_work_type


def normalize_display_text(value: object) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if text.isupper():
        result = text.lower()
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
    return text


def _capitalize_phrase_if_lower(value: str) -> str:
    text = normalize_display_text(value).strip()
    if not text:
        return ""
    if text == text.lower():
        return text[:1].upper() + text[1:]
    return text


_ADMIN_SERVICE_WORDS = frozenset(
    {
        "администрация",
        "администрации",
        "муниципального",
        "муниципальном",
        "муниципальный",
        "муниципальное",
        "муниципальная",
        "образования",
        "образование",
        "района",
        "район",
        "районе",
        "области",
        "область",
        "края",
        "край",
        "округа",
        "округ",
        "республики",
        "республика",
        "поселения",
        "поселение",
        "поселении",
        "городского",
        "городское",
        "городском",
        "городской",
        "сельского",
        "сельское",
        "сельском",
        "сельский",
        "город",
        "города",
        "городе",
        "село",
        "села",
        "селе",
        "деревня",
        "деревни",
        "посёлок",
        "поселок",
        "посёлка",
        "поселка",
        "посёлке",
        "поселке",
        "пгт",
        "сельсовет",
        "сельсовета",
        "поссовет",
        "поссовета",
        "рабочий",
        "рабочего",
        "рабочем",
        "поселок",
        "посёлок",
    }
)

_LOCALITY_MARKERS = frozenset(
    {
        "города",
        "город",
        "села",
        "село",
        "поселка",
        "посёлка",
        "поселок",
        "посёлок",
    }
)

_REPUBLIC_MARKERS = frozenset({"республики", "республика"})

_TOPONYM_WORD_RE = re.compile(
    r"^[а-яё]+(?:"
    r"ского|цкого|нского|овского|евского|инского|"
    r"ской|скую|ская|ский|цкий|"
    r"ого|ому|ом|"
    r"ая|ый|ий|ое|ее"
    r")$",
    re.IGNORECASE,
)


def _title_case_hyphenated_word(word: str) -> str:
    return "-".join(part[:1].upper() + part[1:] if part else part for part in word.split("-"))


def _normalize_geo_word_case(word: str, *, prev_lower: str) -> str:
    clean = word.strip(",.;:")
    if not clean:
        return word

    trailing = word[len(clean) :]
    lower = clean.casefold()
    if lower in _ADMIN_SERVICE_WORDS:
        return lower + trailing
    if prev_lower in _LOCALITY_MARKERS or prev_lower in _REPUBLIC_MARKERS:
        return _title_case_hyphenated_word(lower) + trailing
    if "-" in clean or _TOPONYM_WORD_RE.match(lower):
        return _title_case_hyphenated_word(lower) + trailing
    return clean + trailing


def _normalize_geo_plain_phrase(text: str) -> str:
    words = text.split()
    if not words:
        return text
    normalized_words: list[str] = []
    prev_lower = ""
    for word in words:
        normalized_words.append(_normalize_geo_word_case(word, prev_lower=prev_lower))
        prev_lower = word.strip(",.;:").casefold()
    return " ".join(normalized_words)


def normalize_russian_geo_admin_case(value: str) -> str:
    text = normalize_display_text(value).strip()
    if not text:
        return ""

    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r'("([^"]+)"|«([^»]+)»)', text):
        if match.start() > cursor:
            parts.append(_normalize_geo_plain_phrase(text[cursor : match.start()]))
        if match.group(2) is not None:
            inner = _normalize_mo_name_case(match.group(2))
            parts.append(f'"{inner}"')
        else:
            inner = _normalize_mo_name_case(match.group(3))
            parts.append(f"«{inner}»")
        cursor = match.end()
    if cursor < len(text):
        parts.append(_normalize_geo_plain_phrase(text[cursor:]))
    return "".join(parts).strip()


def _capitalize_words(value: str) -> str:
    words = []
    for word in normalize_display_text(value).split():
        parts = [part[:1].upper() + part[1:] if part else part for part in word.split("-")]
        words.append("-".join(parts))
    return " ".join(words)


def _normalize_mo_tail_case(value: str) -> str:
    text = _capitalize_words(value)
    return re.sub(r"\b(Сельсовет|Поссовет)\b", lambda match: match.group(1).lower(), text)


def _normalize_mo_name_case(value: str) -> str:
    text = normalize_display_text(value).strip()
    if not text:
        return ""
    lower = text.lower()
    if lower.startswith("сельское поселение село "):
        locality = text[len("Сельское поселение село ") :].strip()
        return f"Сельское поселение село {_normalize_mo_tail_case(locality)}"
    if lower.startswith("сельское поселение "):
        tail = text[len("Сельское поселение ") :].strip()
        if tail == tail.lower():
            tail = _normalize_mo_tail_case(tail)
        return f"Сельское поселение {tail}"
    if lower.startswith("городское поселение город "):
        locality = text[len("Городское поселение город ") :].strip()
        return f"Городское поселение город {_capitalize_words(locality)}"
    if lower.startswith("городское поселение поселок "):
        locality = text[len("Городское поселение поселок ") :].strip()
        return f"Городское поселение поселок {_capitalize_words(locality)}"
    return _capitalize_phrase_if_lower(text)


def normalize_municipality_name_case(value: str) -> str:
    """Normalize capitalization in municipality names and administrative phrases."""

    return normalize_russian_geo_admin_case(_normalize_mo_name_case(value))


def _trim_administrative_tail(value: str) -> str:
    text = normalize_display_text(value).strip()
    if not text:
        return ""
    upper = text.upper()
    markers = (
        " МУНИЦИПАЛЬНОГО РАЙОНА ",
        " МУНИЦИПАЛЬНОГО ОКРУГА ",
        " РЕСПУБЛИКИ ",
        " ОБЛАСТИ",
        " КРАЯ",
        " АВТОНОМНОГО ОКРУГА",
    )
    cut = len(text)
    for marker in markers:
        marker_index = upper.find(marker)
        if marker_index > 0:
            cut = min(cut, marker_index)
    trimmed = text[:cut].strip(" ,")
    trimmed = re.sub(
        r"\s+[А-ЯЁа-яё-]+(?:ского|цкого)\s*$",
        "",
        trimmed,
    ).strip(" ,")
    return trimmed or text


def _dedupe_scope_parts(parts: list[str]) -> list[str]:
    result: list[str] = []
    lower_result: list[str] = []
    for raw_part in parts:
        part = normalize_russian_geo_admin_case(raw_part)
        if not part:
            continue
        lower_part = part.lower()
        if any(lower_part == existing for existing in lower_result):
            continue
        if any(lower_part in existing for existing in lower_result):
            continue
        filtered_pairs = [
            (existing_part, existing_lower)
            for existing_part, existing_lower in zip(result, lower_result)
            if existing_lower not in lower_part
        ]
        result = [existing_part for existing_part, _ in filtered_pairs]
        lower_result = [existing_lower for _, existing_lower in filtered_pairs]
        result.append(part)
        lower_result.append(lower_part)
    return result


def ensure_official_district_wording(value: str) -> str:
    text = normalize_display_text(value).strip()
    if not text:
        return ""
    text = re.sub(
        r"(?<!муниципального\s)\b(?!муниципального\b)([А-ЯЁа-яё-]+(?:ского|цкого|ого))\s+района\b",
        r"\1 муниципального района",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"(?<!муниципальный\s)\b(?!муниципальный\b)([А-ЯЁа-яё-]+(?:ский|цкий|ской))\s+район\b",
        r"\1 муниципальный район",
        text,
        flags=re.IGNORECASE,
    )


def build_work_scope_fragment(context: dict) -> str:
    if context.get("DOCUMENT_ENTITY_TYPE") == "district":
        parts = _dedupe_scope_parts(
            [
                str(context.get("MUN_R_NAME_1", "")).strip(),
                str(context.get("SUB_RF_1", "")).strip(),
            ]
        )
        return ensure_official_district_wording(" ".join(parts).strip())

    parts = _dedupe_scope_parts(
        [
            str(context.get("MUN_NAME_2", "")).strip(),
            str(context.get("MUN_R_NAME_1", "")).strip(),
            str(context.get("SUB_RF_1", "")).strip(),
        ]
    )
    return ensure_official_district_wording(" ".join(parts).strip())


def patch_admin_name_components(adm_name: str, row: dict, inflected: Optional[dict] = None) -> str:
    result = adm_name
    for field in ("MUN_NAME", "MUN_R_NAME", "SUB_RF"):
        value = str(row.get(field, "")).strip()
        if value:
            result = result.replace(value.lower(), value)
    if inflected:
        for field in ("MUN_NAME_1", "MUN_R_NAME_1", "SUB_RF_1"):
            value = str(inflected.get(field, "")).strip()
            if value:
                result = result.replace(value.lower(), value)
    return result


def build_unified_admin_name(mun_name: str) -> str:
    normalized_mun_name = normalize_display_text(mun_name).strip()
    if not normalized_mun_name:
        return ""
    return f'Администрация муниципального образования "{normalized_mun_name}"'


def build_district_admin_name(district_name: str) -> str:
    normalized_district_name = ensure_official_district_wording(
        normalize_russian_geo_admin_case(district_name)
    )
    if not normalized_district_name:
        return ""

    from src.generator.inflection.inflect import inflect_mun_name_genitive

    district_genitive = inflect_mun_name_genitive(normalized_district_name).value
    return f"Администрация {district_genitive or normalized_district_name}"


def _looks_like_district_entity_name(value: str) -> bool:
    return is_district_level_entity_name(value)


def _quoted_name_looks_like_mo_name(value: str) -> bool:
    text = normalize_display_text(value).strip()
    if not text:
        return False
    low = text.lower()
    if "район" in low and not any(token in low for token in ("поселение", "округ", "поссовет", "сельсовет")):
        return False
    return any(token in low for token in ("поселение", "округ", "поссовет", "сельсовет"))


def _extract_canonical_mo_from_adm_pattern(adm_name: str) -> str:
    text = normalize_display_text(adm_name).strip()
    if not text:
        return ""

    upper_text = text.upper()
    prefix = "АДМИНИСТРАЦИЯ ГОРОДСКОГО ПОСЕЛЕНИЯ "
    if upper_text.startswith(prefix):
        tail = text[len(prefix) :]
        upper_tail = tail.upper()
        cut = len(tail)
        for marker in (
            " МУНИЦИПАЛЬНОГО РАЙОНА ",
            " МУНИЦИПАЛЬНОГО ОКРУГА ",
            " РЕСПУБЛИКИ ",
            " КРАЯ",
            " ОБЛАСТИ",
            " АВТОНОМНОГО ОКРУГА",
        ):
            marker_index = upper_tail.find(marker)
            if marker_index >= 0:
                cut = min(cut, marker_index)
        candidate = tail[:cut].strip(" ,")
        if not candidate:
            return ""
        upper_candidate = candidate.upper()
        if upper_candidate.startswith("ГОРОД ") or upper_candidate.startswith("ГОРОДА "):
            locality = normalize_display_text(candidate.split(" ", 1)[1].strip()).capitalize() if " " in candidate else ""
            if locality:
                return f"Городское поселение город {locality}"
        if (
            upper_candidate.startswith("ПОСЕЛОК ")
            or upper_candidate.startswith("ПОСЁЛОК ")
            or upper_candidate.startswith("ПОСЕЛКА ")
            or upper_candidate.startswith("ПОСЁЛКА ")
        ):
            locality = normalize_display_text(candidate.split(" ", 1)[1].strip()).capitalize() if " " in candidate else ""
            if locality:
                return f"Городское поселение поселок {locality}"
    return ""


def extract_official_mo_name_from_adm_name(adm_name: str) -> str:
    normalized_adm_name = normalize_display_text(adm_name).strip()
    if not normalized_adm_name:
        return ""
    if contains_nested_administration(normalized_adm_name):
        return normalize_russian_geo_admin_case(
            extract_administration_entity_name(normalized_adm_name)
        )
    quote_match = re.search(r'["«](.+?)["»]', normalized_adm_name)
    if quote_match:
        quoted = normalize_display_text(quote_match.group(1)).strip()
        if _quoted_name_looks_like_mo_name(quoted):
            return _normalize_mo_name_case(_trim_administrative_tail(quoted))
    pattern_name = _extract_canonical_mo_from_adm_pattern(normalized_adm_name)
    if pattern_name:
        return _normalize_mo_name_case(_trim_administrative_tail(pattern_name))
    return ""


def build_requisites(row: dict) -> str:
    oktmo = row.get("REQUISITES_OKTNO", row.get("REQUISITES_OKTMO", ""))
    parts = [
        f"ИНН: {row.get('REQUISITES_INN', '')}",
        f"КПП: {row.get('REQUISITES_KPP', '')}",
        f"ОГРН: {row.get('REQUISITES_OGRN', '')}",
        f"ОКПО: {row.get('REQUISITES_OKPO', '')}",
        f"ОКТМО: {oktmo}",
    ]
    return "\n".join(parts)


WINDOWS_PATH_FORBIDDEN_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_path_component(value: object, *, fallback: str = "unknown", preserve_case: bool = False) -> str:
    """Return a Windows-safe file/folder name without changing source data."""
    text = " ".join(str(value).split()) if preserve_case else normalize_display_text(value)
    text = WINDOWS_PATH_FORBIDDEN_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def build_output_folder_name(row: dict) -> str:
    row_id = sanitize_path_component(row.get("ID", "unknown"))
    safe_name = sanitize_path_component(row.get("MUN_NAME") or row.get("MUN_R_NAME") or "unknown")
    return f"{row_id}_{safe_name}"


def _looks_like_patronymic(word: str) -> bool:
    lower = str(word or "").lower()
    return lower.endswith(
        ("вна", "ична", "инична", "овна", "евна", "ич", "оглы", "кызы", "угли")
    )


def parse_fio_components(fio: str) -> tuple[str, str, str]:
    """Return (surname, first_name, patronymic) from a Russian FIO string."""
    parts = [part for part in str(fio or "").split() if part]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        if _looks_like_patronymic(parts[1]):
            return "", parts[0], parts[1]
        return parts[0], parts[1], ""
    if len(parts) == 1:
        return "", parts[0], ""
    return "", "", ""


def build_first_patronymic(fio: str) -> str:
    surname, first_name, patronymic = parse_fio_components(fio)
    first = first_name or (fio if fio and not surname else "")
    return " ".join(part for part in (first, patronymic) if part).strip()


def build_short_fio(fio: str) -> str:
    parts = [part for part in str(fio).split() if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]

    surname = parts[0]
    initials = []
    for part in parts[1:]:
        initials.append(f"{part[:1].upper()}.")
    return f"{''.join(initials)} {surname}"


def build_population_with_unit(value) -> str:
    raw = str(value).strip()
    if not raw:
        return ""

    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return raw

    number = int(digits)
    mod100 = number % 100
    mod10 = number % 10
    if 11 <= mod100 <= 14:
        unit = "человек"
    elif mod10 == 1:
        unit = "человек"
    elif mod10 in (2, 3, 4):
        unit = "человека"
    else:
        unit = "человек"
    return f"{raw} {unit}"


def build_document_context(row: dict, outgoing_number: int, work_type: str | None = None) -> dict:
    effective_work_type = normalize_work_type(work_type)
    raw_adm_name = normalize_display_text(row.get("ADM_NAME", ""))
    official_mo_name = extract_official_mo_name_from_adm_name(raw_adm_name)
    raw_mun_name = normalize_display_text(row.get("MUN_NAME", ""))
    canonical_raw_mun_name = extract_administration_entity_name(raw_mun_name)
    canonical_adm_entity_name = extract_administration_entity_name(raw_adm_name)
    normalized_mun_r_name = normalize_russian_geo_admin_case(str(row.get("MUN_R_NAME", "")))
    primary_entity_name = (
        canonical_raw_mun_name
        or normalized_mun_r_name
        or canonical_adm_entity_name
    )
    is_district_context = _looks_like_district_entity_name(primary_entity_name)
    district_entity_name = (
        normalized_mun_r_name
        if _looks_like_district_entity_name(normalized_mun_r_name)
        else primary_entity_name
    )
    normalized_mun_name = (
        ensure_official_district_wording(district_entity_name)
        if is_district_context
        else (
            canonical_raw_mun_name
            if contains_nested_administration(raw_adm_name) and canonical_raw_mun_name
            else official_mo_name or canonical_raw_mun_name or raw_mun_name
        )
    )
    adm_name = (
        build_district_admin_name(normalized_mun_name)
        if is_district_context
        else (
            normalize_administration_recipient(raw_adm_name)
            if contains_nested_administration(raw_adm_name)
            else build_unified_admin_name(normalized_mun_name)
        )
    )
    row_for_inflection = dict(row)
    row_for_inflection["MUN_NAME"] = normalized_mun_name
    row_for_inflection["ADM_NAME"] = adm_name
    row_for_inflection["MUN_R_NAME"] = normalized_mun_r_name
    row_for_inflection["SUB_RF"] = normalize_russian_geo_admin_case(str(row.get("SUB_RF", "")))
    row_for_inflection.update(build_work_type_context(effective_work_type))
    if "REQUISITES_OKTNO" not in row_for_inflection and "REQUISITES_OKTMO" in row_for_inflection:
        row_for_inflection["REQUISITES_OKTNO"] = row_for_inflection.get("REQUISITES_OKTMO")
    if "REQUISITES_OKTMO" not in row_for_inflection and "REQUISITES_OKTNO" in row_for_inflection:
        row_for_inflection["REQUISITES_OKTMO"] = row_for_inflection.get("REQUISITES_OKTNO")
    inflected, _ = build_inflected_fields_with_trace(row_for_inflection)
    for field in ("MUN_NAME_1", "MUN_R_NAME_1", "SUB_RF_1", "ADM_NAME_1"):
        value = str(inflected.get(field, "")).strip()
        if value:
            inflected[field] = normalize_russian_geo_admin_case(value)
    adm_name = patch_admin_name_components(adm_name, row_for_inflection, inflected)
    adm_name = normalize_administration_recipient(
        normalize_russian_geo_admin_case(adm_name)
    )
    oktmo = row_for_inflection.get("REQUISITES_OKTNO", row_for_inflection.get("REQUISITES_OKTMO", ""))
    context = {
        "ID": row.get("ID"),
        **build_work_type_context(effective_work_type),
        "DOCUMENT_ENTITY_TYPE": "district" if is_district_context else "municipality",
        "SUB_RF": row_for_inflection["SUB_RF"],
        "MUN_R_NAME": row_for_inflection["MUN_R_NAME"],
        "MUN_NAME": normalized_mun_name,
        "ADM_NAME": adm_name,
        "ADM_NAME_RAW": raw_adm_name,
        "MUN_NAME_RAW": raw_mun_name,
        "ADRES": row.get("ADRES"),
        "HEAD_FIO": row.get("HEAD_FIO"),
        "POPULATION": row.get("POPULATION"),
        "POPULATION_WITH_UNIT": build_population_with_unit(row.get("POPULATION")),
        "EMAIL_OSN": row.get("EMAIL_OSN"),
        "EMAIL_DOP": row.get("EMAIL_DOP"),
        "TEL_OSN": row.get("TEL_OSN"),
        "TEL_DOP": row.get("TEL_DOP"),
        "REQUISITES_INN": row.get("REQUISITES_INN"),
        "REQUISITES_KPP": row.get("REQUISITES_KPP"),
        "REQUISITES_OGRN": row.get("REQUISITES_OGRN"),
        "REQUISITES_OKPO": row.get("REQUISITES_OKPO"),
        "REQUISITES_OKTNO": oktmo,
        "REQUISITES_OKTMO": oktmo,
        "REQUISITES": build_requisites(row),
        "HEAD_FIO_SHORT": build_short_fio(row.get("HEAD_FIO", "")),
        "OUTGOING_NUMBER": outgoing_number,
        "CONTRACT_NUMBER": outgoing_number,
        "DATE": datetime.now().strftime("%d.%m.%Y"),
    }
    context.update(inflected)
    context["ADM_NAME"] = normalize_administration_recipient(context.get("ADM_NAME"))
    context["ADM_NAME_1"] = normalize_administration_recipient(context.get("ADM_NAME_1"))
    context.update(build_work_type_context(effective_work_type))
    context["HEAD_MO_FRAGMENT"] = (
        str(context.get("MUN_R_NAME_1", "")).strip()
        if is_district_context
        else str(context.get("MUN_NAME_1", "")).strip()
    )
    context["WORK_SCOPE_FRAGMENT"] = build_work_scope_fragment(context)
    context["MUN_R_SCOPE_FRAGMENT"] = ensure_official_district_wording(
        f"{context.get('MUN_R_NAME_1', '')} {context.get('SUB_RF_1', '')}".strip()
    )
    for field in (
        "MUN_R_NAME",
        "SUB_RF",
        "MUN_R_NAME_1",
        "SUB_RF_1",
        "ADM_NAME_1",
        "MUN_NAME_1",
        "WORK_SCOPE_FRAGMENT",
        "MUN_R_SCOPE_FRAGMENT",
        "HEAD_MO_FRAGMENT",
    ):
        value = str(context.get(field, "")).strip()
        if value:
            context[field] = normalize_russian_geo_admin_case(value)
    return context
