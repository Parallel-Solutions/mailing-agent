from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openpyxl import load_workbook


HEADER_ROW = 2
DATA_START_ROW = 3
MUN_NAME_COLUMN = "MUN_NAME"
ADM_NAME_COLUMN = "ADM_NAME"

ORIGINAL_COLUMN = "MUN_NAME_SOURCE_ORIGINAL"
OFFICIAL_COLUMN = "MUN_NAME_OFFICIAL"
STATUS_COLUMN = "MUN_NAME_VERIFICATION_STATUS"
CONFIDENCE_COLUMN = "MUN_NAME_VERIFICATION_CONFIDENCE"
SOURCE_COLUMN = "MUN_NAME_VERIFICATION_SOURCE"
REASON_COLUMN = "MUN_NAME_VERIFICATION_REASON"
URL_COLUMN = "MUN_NAME_SOURCE_URL"

VERIFICATION_COLUMNS = (
    ORIGINAL_COLUMN,
    OFFICIAL_COLUMN,
    STATUS_COLUMN,
    CONFIDENCE_COLUMN,
    SOURCE_COLUMN,
    REASON_COLUMN,
    URL_COLUMN,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASE_XLSX_PATH = PROJECT_ROOT / "service_docs" / "base.xlsx"

ADMINISTRATION_PREFIX_RE = re.compile(
    r"^\s*(?:администрация|администрации)\s+(?:муниципального\s+образования\s+)?",
    re.IGNORECASE,
)
MUNICIPAL_FORM_RE = re.compile(
    r"\b("
    r"сельское\s+поселение|городское\s+поселение|муниципальный\s+округ|"
    r"городской\s+округ|муниципальное\s+образование|поселок|посёлок|"
    r"сельсовет|город\s+[^,]+"
    r")\b",
    re.IGNORECASE,
)
QUOTE_RE = re.compile(r"[\"«„](.*?)[\"»“]")
OFFICIAL_SITE_EXCLUDED_DOMAINS = (
    "audit-it.ru",
    "checko.ru",
    "egrul.nalog.ru",
    "kartoteka.ru",
    "list-org.com",
    "nalog.ru",
    "rusprofile.ru",
    "sbis.ru",
    "spark-interfax.ru",
    "synapsenet.ru",
    "vbankcenter.ru",
    "zachestnyibiznes.ru",
    "zakupki.gov.ru",
)
OFFICIAL_SITE_DOMAIN_HINTS = (
    ".gov.ru",
    ".gosuslugi.ru",
    ".mosreg.ru",
    ".tatarstan.ru",
    ".bashkortostan.ru",
    ".lenobl.ru",
    ".ryazangov.ru",
)
OFFICIAL_SITE_URL_HINTS = (
    "adm",
    "admin",
    "mo-",
    "mun",
    "poselen",
    "selsovet",
    "сельсовет",
)


@dataclass(frozen=True)
class MunicipalityNameVerification:
    row_id: str
    original_name: str
    official_name: str
    status: str
    confidence: str
    source: str
    reason: str
    source_url: str = ""

    @property
    def should_replace(self) -> bool:
        return self.status == "verified" and self.confidence == "high" and bool(self.official_name)


@dataclass(frozen=True)
class OfficialSiteMatch:
    url: str
    title: str
    content: str
    score: int


@dataclass(frozen=True)
class BaseMunicipalityEntry:
    sub_rf: str
    mun_r_name: str
    mun_name: str


def verify_municipality_name(
    row: dict[str, Any],
    *,
    base_entries: list[BaseMunicipalityEntry] | None = None,
) -> MunicipalityNameVerification:
    """Choose the best local official-name candidate from row data.

    This is the deterministic first layer. It only trusts explicit wording in
    the uploaded table, primarily quoted names from ADM_NAME. External official
    website search can be layered on top later without changing this contract.
    """

    current_name = _clean(row.get(MUN_NAME_COLUMN))
    adm_name = _clean(row.get(ADM_NAME_COLUMN))

    base_name = _find_base_municipality_name(row, base_entries or [])
    if base_name:
        return MunicipalityNameVerification(
            row_id=_clean(row.get("ID")),
            original_name=current_name,
            official_name=normalize_municipality_display_name(base_name),
            status="verified",
            confidence="high",
            source="base.xlsx",
            reason="Название подтверждено локальной базой муниципальных образований по субъекту, району и типу МО.",
        )

    quoted_candidate = _extract_quoted_municipality_name(adm_name, current_name)
    if quoted_candidate:
        normalized = normalize_municipality_display_name(quoted_candidate)
        return MunicipalityNameVerification(
            row_id=_clean(row.get("ID")),
            original_name=current_name,
            official_name=normalized,
            status="verified",
            confidence="high",
            source="ADM_NAME",
            reason="Название извлечено из кавычек в полном названии администрации.",
        )

    if _adm_name_confirms_current_municipality(adm_name, current_name):
        return MunicipalityNameVerification(
            row_id=_clean(row.get("ID")),
            original_name=current_name,
            official_name=normalize_municipality_display_name(current_name),
            status="verified",
            confidence="medium",
            source="ADM_NAME+MUN_NAME",
            reason="MUN_NAME согласован с названием администрации; замена не требуется.",
        )

    stripped_candidate = _extract_from_administration_name(adm_name)
    if stripped_candidate:
        normalized = normalize_municipality_display_name(stripped_candidate)
        return MunicipalityNameVerification(
            row_id=_clean(row.get("ID")),
            original_name=current_name,
            official_name=normalized,
            status="kept",
            confidence="medium",
            source="ADM_NAME",
            reason="Название получено после удаления слова «Администрация», но без явного официального подтверждения; исходный MUN_NAME оставлен.",
        )

    if current_name:
        return MunicipalityNameVerification(
            row_id=_clean(row.get("ID")),
            original_name=current_name,
            official_name=normalize_municipality_display_name(current_name),
            status="kept",
            confidence="low",
            source="MUN_NAME",
            reason="В ADM_NAME не найдено надежного официального названия; оставлен исходный MUN_NAME.",
        )

    return MunicipalityNameVerification(
        row_id=_clean(row.get("ID")),
        original_name=current_name,
        official_name="",
        status="missing",
        confidence="low",
        source="",
        reason="Не заполнены MUN_NAME и ADM_NAME.",
    )


def verify_municipality_names_in_workbook(
    xlsx_path: Path,
    *,
    use_official_sites: bool = False,
    base_xlsx_path: Path | None = None,
) -> dict[str, Any]:
    """Update data.xlsx with municipality-name verification.

    The local deterministic layer is always used. Official-site search is an
    optional production layer because it depends on an external API and can take
    noticeable time for large tables.
    """

    workbook = load_workbook(xlsx_path)
    worksheet = workbook[workbook.sheetnames[0]]
    header_map = _ensure_headers(worksheet)
    base_entries = load_base_municipality_entries(base_xlsx_path)

    mun_col = header_map.get(MUN_NAME_COLUMN)
    if not mun_col:
        workbook.close()
        return {
            "status": "skipped",
            "updated_rows": 0,
            "verified_rows": 0,
            "kept_rows": 0,
            "replacements": [],
            "replacement_samples": [],
            "reason": "В таблице нет колонки MUN_NAME.",
        }

    stats = {
        "status": "ok",
        "total_rows": 0,
        "updated_rows": 0,
        "verified_rows": 0,
        "kept_rows": 0,
        "missing_rows": 0,
        "official_site_checked_rows": 0,
        "official_site_found_rows": 0,
        "official_site_error_rows": 0,
        "replacements": [],
        "replacement_samples": [],
    }

    for row_index in range(DATA_START_ROW, worksheet.max_row + 1):
        row = _read_row(worksheet, header_map, row_index)
        if not any(value not in (None, "") for value in row.values()):
            continue
        stats["total_rows"] += 1
        verification = verify_municipality_name(row, base_entries=base_entries)
        if use_official_sites:
            verification = _verify_with_official_site(row, verification, stats)
        _write_verification(worksheet, header_map, row_index, verification)

        if verification.status == "verified":
            stats["verified_rows"] += 1
        elif verification.status == "kept":
            stats["kept_rows"] += 1
        elif verification.status == "missing":
            stats["missing_rows"] += 1

        current_name = _clean(row.get(MUN_NAME_COLUMN))
        if verification.should_replace and verification.official_name != current_name:
            worksheet.cell(row=row_index, column=mun_col).value = verification.official_name
            stats["updated_rows"] += 1
            replacement = {
                "row_id": verification.row_id or str(row_index - HEADER_ROW),
                "from": current_name,
                "to": verification.official_name,
                "source": verification.source,
                "confidence": verification.confidence,
            }
            stats["replacements"].append(replacement)
            if len(stats["replacement_samples"]) < 20:
                stats["replacement_samples"].append(replacement)

    workbook.save(xlsx_path)
    workbook.close()
    return stats


def _verify_with_official_site(
    row: dict[str, Any],
    local_verification: MunicipalityNameVerification,
    stats: dict[str, Any],
) -> MunicipalityNameVerification:
    stats["official_site_checked_rows"] += 1
    try:
        match = find_official_site_for_municipality(row)
    except Exception as exc:
        stats["official_site_error_rows"] += 1
        return _with_reason_suffix(
            local_verification,
            f"Проверка официального сайта не выполнена: {exc}",
        )

    if not match:
        return _with_reason_suffix(local_verification, "Официальный сайт по строке не найден.")

    stats["official_site_found_rows"] += 1
    if local_verification.status == "verified":
        return MunicipalityNameVerification(
            row_id=local_verification.row_id,
            original_name=local_verification.original_name,
            official_name=local_verification.official_name,
            status="verified",
            confidence="high" if local_verification.confidence == "high" else "medium",
            source=f"{local_verification.source}+official_site",
            reason=f"{local_verification.reason} Найден официальный источник: {match.title or match.url}.",
            source_url=match.url,
        )

    current_name = _clean(row.get(MUN_NAME_COLUMN))
    if current_name and _text_contains_municipality_name(f"{match.title} {match.content}", current_name):
        return MunicipalityNameVerification(
            row_id=local_verification.row_id,
            original_name=local_verification.original_name,
            official_name=normalize_municipality_display_name(current_name),
            status="verified",
            confidence="medium",
            source="official_site",
            reason=f"MUN_NAME найден в официальном источнике: {match.title or match.url}. Замена не требуется.",
            source_url=match.url,
        )

    return MunicipalityNameVerification(
        row_id=local_verification.row_id,
        original_name=local_verification.original_name,
        official_name=local_verification.official_name,
        status=local_verification.status,
        confidence=local_verification.confidence,
        source=f"{local_verification.source}+official_site",
        reason=f"{local_verification.reason} Официальный источник найден, но название в сниппете не подтверждено автоматически.",
        source_url=match.url,
    )


def find_official_site_for_municipality(row: dict[str, Any]) -> OfficialSiteMatch | None:
    try:
        from tavily import TavilyClient
        from src.utils.config import settings
    except Exception:
        return None

    if not settings.tavily_api_key:
        return None

    query = _build_official_site_query(row)
    if not query:
        return None

    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(
        query=query,
        search_depth="basic",
        max_results=8,
        include_answer=False,
    )
    matches = [
        match
        for result in response.get("results", [])
        if (match := _official_site_match_from_search_result(result, row))
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.score, reverse=True)[0]


def _build_official_site_query(row: dict[str, Any]) -> str:
    adm_name = _clean(row.get(ADM_NAME_COLUMN))
    mun_name = _clean(row.get(MUN_NAME_COLUMN))
    sub_rf = _clean(row.get("SUB_RF"))
    if adm_name:
        return f'"{adm_name}" официальный сайт администрация'
    if mun_name:
        return f'"{mun_name}" официальный сайт администрация {sub_rf}'.strip()
    return ""


def _official_site_match_from_search_result(result: dict[str, Any], row: dict[str, Any]) -> OfficialSiteMatch | None:
    url = _clean(result.get("url"))
    if not url:
        return None
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    if not domain or any(domain == item or domain.endswith(f".{item}") for item in OFFICIAL_SITE_EXCLUDED_DOMAINS):
        return None

    title = _clean(result.get("title"))
    content = _clean(result.get("content"))
    haystack = f"{title} {content}".lower()
    score = 0
    if any(hint in domain for hint in OFFICIAL_SITE_DOMAIN_HINTS):
        score += 5
    if any(hint in url.lower() for hint in OFFICIAL_SITE_URL_HINTS):
        score += 3
    if "официальный" in haystack:
        score += 2
    if "администрац" in haystack:
        score += 2
    if _text_contains_municipality_name(f"{title} {content}", _clean(row.get(MUN_NAME_COLUMN))):
        score += 3
    if _adm_name_mentions_search_result(_clean(row.get(ADM_NAME_COLUMN)), f"{title} {content}"):
        score += 3
    if score < 3:
        return None
    return OfficialSiteMatch(url=url, title=title, content=content, score=score)


def _text_contains_municipality_name(text: str, municipality_name: str) -> bool:
    normalized_text = _normalize_for_match(text)
    keyword = _municipality_keyword_base(_normalize_for_match(municipality_name))
    if not normalized_text or not keyword:
        return False
    return keyword in normalized_text or keyword.removesuffix("ск") in normalized_text


def _adm_name_mentions_search_result(adm_name: str, text: str) -> bool:
    normalized_text = _normalize_for_match(text)
    for word in _administration_municipality_name_words(_normalize_for_match(adm_name)):
        base = _adjective_base(word)
        if base and (base in normalized_text or base.removesuffix("ск") in normalized_text):
            return True
    return False


def _with_reason_suffix(
    verification: MunicipalityNameVerification,
    suffix: str,
) -> MunicipalityNameVerification:
    return MunicipalityNameVerification(
        row_id=verification.row_id,
        original_name=verification.original_name,
        official_name=verification.official_name,
        status=verification.status,
        confidence=verification.confidence,
        source=verification.source,
        reason=f"{verification.reason} {suffix}",
        source_url=verification.source_url,
    )


def load_base_municipality_entries(base_xlsx_path: Path | None = None) -> list[BaseMunicipalityEntry]:
    path = base_xlsx_path or DEFAULT_BASE_XLSX_PATH
    if not path.exists():
        return []
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except OSError:
        return []

    try:
        worksheet = workbook[workbook.sheetnames[0]]
        rows = worksheet.iter_rows(values_only=True)
        headers = [_clean(value) for value in (next(rows, None) or [])]
        sub_index = _find_header_index(headers, "Субъект РФ")
        district_index = _find_header_index(headers, "Муниципальный район")
        municipality_index = _find_header_index(headers, "Муниципальное образование")
        if sub_index is None or district_index is None or municipality_index is None:
            return []

        entries: list[BaseMunicipalityEntry] = []
        for row in rows:
            sub_rf = _clean(_row_value(row, sub_index))
            mun_r_name = _clean(_row_value(row, district_index))
            mun_name = _clean(_row_value(row, municipality_index))
            if not sub_rf or not mun_name or not _looks_like_municipality(mun_name):
                continue
            entries.append(BaseMunicipalityEntry(sub_rf=sub_rf, mun_r_name=mun_r_name, mun_name=mun_name))
        return entries
    finally:
        workbook.close()


def _find_base_municipality_name(row: dict[str, Any], entries: list[BaseMunicipalityEntry]) -> str:
    if not entries:
        return ""

    sub_rf = _normalize_for_match(row.get("SUB_RF"))
    mun_r_name = _normalize_for_match(row.get("MUN_R_NAME"))
    candidates = [
        _clean(row.get(MUN_NAME_COLUMN)),
        _extract_quoted_municipality_name(_clean(row.get(ADM_NAME_COLUMN)), _clean(row.get(MUN_NAME_COLUMN))),
        _extract_from_administration_name(_clean(row.get(ADM_NAME_COLUMN))),
    ]
    candidates = [candidate for candidate in dict.fromkeys(candidates) if candidate]
    if not candidates:
        return ""

    best_name = ""
    best_score = 0
    for entry in entries:
        if sub_rf and _normalize_for_match(entry.sub_rf) != sub_rf:
            continue
        if mun_r_name and _normalize_for_match(entry.mun_r_name) != mun_r_name:
            continue
        score = _score_base_municipality_match(entry.mun_name, candidates)
        if score > best_score:
            best_score = score
            best_name = entry.mun_name

    return best_name if best_score >= 80 else ""


def _score_base_municipality_match(base_name: str, candidates: list[str]) -> int:
    base_norm = _normalize_for_match(base_name)
    base_keyword = _municipality_keyword_base(base_norm)
    base_type = _municipality_type(base_name)
    best_score = 0
    for candidate in candidates:
        candidate_norm = _normalize_for_match(candidate)
        candidate_keyword = _municipality_keyword_base(candidate_norm)
        candidate_type = _municipality_type(candidate)
        score = 0
        if candidate_norm == base_norm:
            score += 100
        if candidate_keyword and base_keyword and _adjective_bases_match(candidate_keyword, base_keyword):
            score += 55
        if candidate_type and base_type and candidate_type == base_type:
            score += 35
        elif candidate_type and base_type and candidate_type != base_type:
            score -= 50
        if candidate_norm and (candidate_norm in base_norm or base_norm in candidate_norm):
            score += 20
        best_score = max(best_score, score)
    return best_score


def _municipality_type(value: str) -> str:
    lowered = _normalize_for_match(value)
    type_patterns = (
        ("urban_settlement", "городское поселение"),
        ("rural_settlement", "сельское поселение"),
        ("municipal_district", "муниципальный район"),
        ("municipal_district", "муниципального района"),
        ("municipal_okrug", "муниципальный округ"),
        ("urban_okrug", "городской округ"),
        ("selsovet", "сельсовет"),
    )
    for result, marker in type_patterns:
        if marker in lowered:
            return result
    return ""


def _find_header_index(headers: list[str], expected: str) -> int | None:
    expected_norm = _normalize_for_match(expected)
    for index, header in enumerate(headers):
        if expected_norm and expected_norm in _normalize_for_match(header):
            return index
    return None


def _row_value(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if index < len(row) else None


def normalize_municipality_display_name(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = text.strip("\"«»“”„")
    if _is_mostly_upper(text):
        text = text.lower()
        text = _restore_municipality_capitalization(text)
    text = _normalize_quotes_spacing(text)
    return text


def _ensure_headers(worksheet) -> dict[str, int]:
    header_map = {
        str(worksheet.cell(row=HEADER_ROW, column=column_index).value).strip(): column_index
        for column_index in range(1, worksheet.max_column + 1)
        if worksheet.cell(row=HEADER_ROW, column=column_index).value
    }
    next_column = worksheet.max_column + 1
    for header in VERIFICATION_COLUMNS:
        if header not in header_map:
            worksheet.cell(row=HEADER_ROW, column=next_column).value = header
            header_map[header] = next_column
            next_column += 1
    return header_map


def _read_row(worksheet, header_map: dict[str, int], row_index: int) -> dict[str, Any]:
    return {
        header: worksheet.cell(row=row_index, column=column_index).value
        for header, column_index in header_map.items()
    }


def _write_verification(
    worksheet,
    header_map: dict[str, int],
    row_index: int,
    verification: MunicipalityNameVerification,
) -> None:
    values = {
        ORIGINAL_COLUMN: verification.original_name,
        OFFICIAL_COLUMN: verification.official_name,
        STATUS_COLUMN: verification.status,
        CONFIDENCE_COLUMN: verification.confidence,
        SOURCE_COLUMN: verification.source,
        REASON_COLUMN: verification.reason,
        URL_COLUMN: verification.source_url,
    }
    for header, value in values.items():
        worksheet.cell(row=row_index, column=header_map[header]).value = value


def _extract_quoted_municipality_name(adm_name: str, current_name: str = "") -> str:
    if not adm_name:
        return ""
    for match in QUOTE_RE.finditer(adm_name):
        candidate = _clean(match.group(1))
        if _looks_like_municipality(candidate):
            if _is_bare_locality_name(candidate) and _looks_like_settlement(current_name):
                contextual_candidate = _compose_contextual_quoted_name(adm_name, candidate)
                if contextual_candidate and _looks_like_municipality(contextual_candidate):
                    return contextual_candidate
            return candidate
        contextual_candidate = _compose_contextual_quoted_name(adm_name, candidate)
        if contextual_candidate and _looks_like_municipality(contextual_candidate):
            return contextual_candidate
    return ""


def _extract_from_administration_name(adm_name: str) -> str:
    if not adm_name:
        return ""
    without_prefix = ADMINISTRATION_PREFIX_RE.sub("", adm_name).strip()
    without_prefix = without_prefix.strip("\"«»“”„")
    if without_prefix and without_prefix != adm_name and _looks_like_municipality(without_prefix):
        return without_prefix
    return ""


def _looks_like_municipality(value: str) -> bool:
    text = _clean(value)
    return bool(text and MUNICIPAL_FORM_RE.search(text))


def _looks_like_settlement(value: str) -> bool:
    lowered = _clean(value).lower()
    return "городское поселение" in lowered or "сельское поселение" in lowered


def _is_bare_locality_name(value: str) -> bool:
    lowered = _clean(value).lower()
    return bool(re.match(r"^(?:город|пос[её]лок|п\.|село|деревня)\b", lowered))


def _compose_contextual_quoted_name(adm_name: str, candidate: str) -> str:
    adm_text = _clean(adm_name).lower()
    if "городского поселения" in adm_text or "городское поселение" in adm_text:
        return f"Городское поселение {_normalize_locality_fragment(candidate)}".strip()
    if "сельского поселения" in adm_text or "сельское поселение" in adm_text:
        return f"Сельское поселение {_normalize_locality_fragment(candidate)}".strip()
    return ""


def _normalize_locality_fragment(value: str) -> str:
    text = _clean(value).strip("\"«»“”„")
    text = re.sub(r"(?i)^п\.\s*", "поселок ", text)
    if _is_mostly_upper(text):
        text = text.lower()
    words = text.split()
    if not words:
        return ""
    if words[0].lower() in {"город", "поселок", "посёлок", "село", "деревня"}:
        words[0] = words[0].lower()
        words[1:] = [_readable_capitalized_word(word) for word in words[1:]]
        return " ".join(words)
    return " ".join(_readable_capitalized_word(word) for word in words)


def _adm_name_confirms_current_municipality(adm_name: str, current_name: str) -> bool:
    """Confirm common administration names against current MUN_NAME.

    These names do not contain the official municipality name in quotes, so we
    should not rewrite MUN_NAME from them. Still, they are strong enough to
    confirm that the current row is internally consistent.
    """

    adm_text = _clean(adm_name).lower()
    mun_text = _clean(current_name).lower()
    if not adm_text or not mun_text:
        return False
    mun_keyword = _municipality_keyword_base(mun_text)
    if not mun_keyword:
        return False
    for adm_word in _administration_municipality_name_words(adm_text):
        if _adjective_bases_match(_adjective_base(adm_word), mun_keyword):
            return True
    return False


def _municipality_keyword_base(mun_text: str) -> str:
    for pattern in (
        r"\bсельское\s+поселение\s+([а-яё-]+)",
        r"\bгородское\s+поселение\s+([а-яё-]+)",
    ):
        match = re.search(pattern, mun_text, re.IGNORECASE)
        if match:
            return _adjective_base(match.group(1))
    return _adjective_base(_first_word(mun_text))


def _administration_municipality_name_words(adm_text: str) -> list[str]:
    patterns = (
        r"\b([а-яё-]+)\s+сельская\s+администрация\b",
        r"\bадминистрация\s+(?:муниципального\s+образования\s+[-–—]?\s*)?([а-яё-]+)\s+сельское\s+поселение\b",
        r"\bадминистрация\s+(?:муниципального\s+образования\s+[-–—]?\s*)?([а-яё-]+)\s+городское\s+поселение\b",
        r"\bадминистрация\s+(?:муниципального\s+образования\s+[-–—]?\s*)?([а-яё-]+)\s+сельсовет\b",
        r"\bадминистрация\s+([а-яё-]+)\s+сельского\s+поселения\b",
        r"\bадминистрация\s+([а-яё-]+)\s+городского\s+поселения\b",
        r"\bадминистрация\s+([а-яё-]+)\s+сельсовета\b",
        r"\bадминистрация\s+муниципального\s+образования\s+сельского\s+поселения\s+([а-яё-]+)\b",
        r"\bадминистрация\s+муниципального\s+образования\s+городского\s+поселения\s+([а-яё-]+)\b",
        r"\bадминистрация\s+сельского\s+поселения\s+([а-яё-]+)\b",
        r"\bадминистрация\s+городского\s+поселения\s+([а-яё-]+)\b",
    )
    return [match.group(1) for pattern in patterns for match in re.finditer(pattern, adm_text, re.IGNORECASE)]


def _first_word(value: str) -> str:
    match = re.search(r"[а-яёА-ЯЁ-]+", value)
    return match.group(0) if match else ""


def _adjective_base(value: str) -> str:
    word = value.lower().replace("ё", "е").strip("-")
    for ending in (
        "ского",
        "цкого",
        "ская",
        "цкая",
        "ское",
        "цкое",
        "ого",
        "его",
        "ая",
        "ое",
        "яя",
        "ее",
        "ий",
        "ый",
        "ой",
    ):
        if word.endswith(ending) and len(word) > len(ending) + 2:
            return word[: -len(ending)]
    return word


def _adjective_bases_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return left.removesuffix("ск") == right.removesuffix("ск")


def _is_mostly_upper(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    if not letters:
        return False
    upper_letters = [char for char in letters if char.isupper()]
    return len(upper_letters) / len(letters) > 0.8


def _title_first_word(value: str) -> str:
    words = value.split(" ")
    if not words:
        return value
    words[0] = _capitalize_hyphenated(words[0])
    return " ".join(words)


def _restore_municipality_capitalization(value: str) -> str:
    text = _title_first_word(value)
    words = text.split(" ")
    if words and words[0].lower() in {"город", "поселок", "посёлок", "село", "деревня"}:
        words[0] = words[0].lower()
        words[1:] = [_readable_capitalized_word(word) for word in words[1:]]
        return " ".join(words)
    prefixes = (
        ("Сельское", "поселение"),
        ("Городское", "поселение"),
        ("Муниципальный", "округ"),
        ("Городской", "округ"),
        ("Муниципальное", "образование"),
    )
    for prefix in prefixes:
        if len(words) > len(prefix) and tuple(words[: len(prefix)]) == prefix:
            tail_index = len(prefix)
            if words[tail_index].lower() in {"город", "село", "поселок", "посёлок", "деревня"}:
                words[tail_index] = words[tail_index].lower()
                if len(words) > tail_index + 1:
                    words[tail_index + 1] = _capitalize_hyphenated(words[tail_index + 1])
            else:
                words[tail_index] = _capitalize_hyphenated(words[tail_index])
            return " ".join(words)
    return text


def _capitalize_hyphenated(value: str) -> str:
    return "-".join(part[:1].upper() + part[1:] for part in value.split("-") if part)


def _readable_capitalized_word(value: str) -> str:
    word = value.lower() if _is_mostly_upper(value) else value
    return _capitalize_hyphenated(word)


def _normalize_quotes_spacing(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = text.replace(" ,", ",").replace(" .", ".")
    return text


def _normalize_for_match(value: Any) -> str:
    text = _clean(value).lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()
