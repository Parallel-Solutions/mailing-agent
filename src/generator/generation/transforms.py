from datetime import datetime
import re
from typing import Optional

from src.generator.case_engine import build_inflected_fields_with_trace
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
        part = _capitalize_phrase_if_lower(raw_part)
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
    return re.sub(
        r"(?<!муниципального\s)\b(?!муниципального\b)([А-ЯЁа-яё-]+(?:ского|цкого|ого))\s+района\b",
        r"\1 муниципального района",
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
    normalized_district_name = normalize_display_text(district_name).strip()
    if not normalized_district_name:
        return ""
    return f"Администрация муниципального образования {normalized_district_name}"


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


def sanitize_path_component(value: object, *, fallback: str = "unknown") -> str:
    """Return a Windows-safe file/folder name without changing source data."""
    text = normalize_display_text(value)
    text = WINDOWS_PATH_FORBIDDEN_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def build_output_folder_name(row: dict) -> str:
    row_id = sanitize_path_component(row.get("ID", "unknown"))
    safe_name = sanitize_path_component(row.get("MUN_NAME") or row.get("MUN_R_NAME") or "unknown")
    return f"{row_id}_{safe_name}"


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
    normalized_mun_r_name = _capitalize_phrase_if_lower(row.get("MUN_R_NAME", ""))
    is_district_context = not raw_mun_name and bool(normalized_mun_r_name)
    normalized_mun_name = normalized_mun_r_name if is_district_context else official_mo_name or raw_mun_name
    adm_name = (
        raw_adm_name or build_district_admin_name(normalized_mun_r_name)
        if is_district_context
        else build_unified_admin_name(normalized_mun_name)
    )
    row_for_inflection = dict(row)
    row_for_inflection["MUN_NAME"] = normalized_mun_name
    row_for_inflection["ADM_NAME"] = adm_name
    row_for_inflection["MUN_R_NAME"] = normalized_mun_r_name
    row_for_inflection["SUB_RF"] = _capitalize_phrase_if_lower(row.get("SUB_RF", ""))
    row_for_inflection.update(build_work_type_context(effective_work_type))
    if "REQUISITES_OKTNO" not in row_for_inflection and "REQUISITES_OKTMO" in row_for_inflection:
        row_for_inflection["REQUISITES_OKTNO"] = row_for_inflection.get("REQUISITES_OKTMO")
    if "REQUISITES_OKTMO" not in row_for_inflection and "REQUISITES_OKTNO" in row_for_inflection:
        row_for_inflection["REQUISITES_OKTMO"] = row_for_inflection.get("REQUISITES_OKTNO")
    inflected, _ = build_inflected_fields_with_trace(row_for_inflection)
    adm_name = patch_admin_name_components(adm_name, row_for_inflection, inflected)
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
    context.update(build_work_type_context(effective_work_type))
    context["HEAD_MO_FRAGMENT"] = (
        str(context.get("MUN_R_NAME_1", "")).strip()
        if is_district_context
        else str(context.get("MUN_NAME_1", "")).strip()
    )
    context["WORK_SCOPE_FRAGMENT"] = build_work_scope_fragment(context)
    return context
