from datetime import datetime
import re
from typing import Optional

from src.generator.inflect import build_inflected_fields


def normalize_display_text(value: str) -> str:
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
            return quoted
    pattern_name = _extract_canonical_mo_from_adm_pattern(normalized_adm_name)
    if pattern_name:
        return pattern_name
    return ""


def build_requisites(row: dict) -> str:
    parts = [
        f"ИНН: {row.get('REQUISITES_INN', '')}",
        f"КПП: {row.get('REQUISITES_KPP', '')}",
        f"ОГРН: {row.get('REQUISITES_OGRN', '')}",
        f"ОКПО: {row.get('REQUISITES_OKPO', '')}",
        f"ОКТМО: {row.get('REQUISITES_OKTNO', '')}",
    ]
    return "\n".join(parts)


def build_output_folder_name(row: dict) -> str:
    row_id = row.get("ID", "unknown")
    mun_name = str(row.get("MUN_NAME", "unknown")).strip()
    safe_name = mun_name.replace("/", "-").replace("\\", "-")
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


def build_document_context(row: dict, outgoing_number: int) -> dict:
    raw_adm_name = normalize_display_text(row.get("ADM_NAME", ""))
    official_mo_name = extract_official_mo_name_from_adm_name(raw_adm_name)
    normalized_mun_name = official_mo_name or normalize_display_text(row.get("MUN_NAME", ""))
    adm_name = build_unified_admin_name(normalized_mun_name)
    row_for_inflection = dict(row)
    row_for_inflection["MUN_NAME"] = normalized_mun_name
    row_for_inflection["ADM_NAME"] = adm_name
    inflected = build_inflected_fields(row_for_inflection)
    context = {
        "ID": row.get("ID"),
        "SUB_RF": row.get("SUB_RF"),
        "MUN_R_NAME": row.get("MUN_R_NAME"),
        "MUN_NAME": normalized_mun_name,
        "ADM_NAME": adm_name,
        "ADM_NAME_RAW": raw_adm_name,
        "MUN_NAME_RAW": normalize_display_text(row.get("MUN_NAME", "")),
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
        "REQUISITES_OKTNO": row.get("REQUISITES_OKTNO"),
        "REQUISITES": build_requisites(row),
        "HEAD_FIO_SHORT": build_short_fio(row.get("HEAD_FIO", "")),
        "OUTGOING_NUMBER": outgoing_number,
        "CONTRACT_NUMBER": outgoing_number,
        "DATE": datetime.now().strftime("%d.%m.%Y"),
    }
    context.update(inflected)
    context["HEAD_MO_FRAGMENT"] = str(context.get("MUN_NAME_1", "")).strip()
    work_scope_parts = [
        str(context.get("MUN_NAME_2", "")).strip(),
        str(context.get("MUN_R_NAME_1", "")).strip(),
        str(context.get("SUB_RF_1", "")).strip(),
    ]
    context["WORK_SCOPE_FRAGMENT"] = " ".join(part for part in work_scope_parts if part).strip()
    return context
