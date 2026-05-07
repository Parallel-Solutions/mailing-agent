"""
Слияние данных из трёх источников:
- data/base.xlsx  — база МО (субъект, район, МО, население)
- data/data.xlsx  — уже обработанные записи (пропускаем)
- data/RMZ7KH.xlsx — справочник организаций

Алгоритм:
1. Читаем baseMO — только первый лист
2. Читаем data.xlsx — собираем уже обработанные МО (по MUN_NAME)
3. Читаем RMZ7KH — только первый лист, фильтруем администрации
4. Для каждого МО из baseMO которого нет в data.xlsx — ищем в RMZ7KH
5. Записываем найденное в data.xlsx
6. Возвращаем список спорных совпадений для проверки агентом
"""

import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from src.parser.excel_writer import ExcelWriter, MoRecord
from src.utils.logger import logger

BASE_MO_PATH = Path("service_docs/base.xlsx")
RMZ_PATH = Path("service_docs/RMZ7KH.xlsx")
DATA_XLSX_PATH = Path("data/data.xlsx")

# Слова-шум при извлечении ключей из названия МО
NOISE_WORDS = {
    "сельское", "поселение", "поселения", "сельской",
    "поселок", "посёлок", "рабочего", "рабочий",
    "пгт", "гп", "рп", "кп", "город",
    "муниципальное", "муниципального", "образование", "образования",
}

# Шум при нормализации региона
REGION_NOISE = {
    "республика", "область", "край", "округ", "автономная", "автономный",
    "автономной", "федерального", "значения", "город", "г", "и", "ао",
    "федеральный",
}

# Шум при нормализации района
DISTRICT_NOISE = {
    "муниципальный", "муниципального", "район", "административный",
    "административного", "городской", "городского", "округ",
}

# Разрешённые и запрещённые маркеры организации
ALLOWED_MARKERS = ["администрация", "муниципальное образование"]
BANNED_MARKERS = [
    "совет депутатов", "совет народных депутатов",
    "бюджетное учреждение", "казенное учреждение", "казённое учреждение",
    "автономное учреждение", "школа", "детский", "дошкольное",
]

BRACKET_RE = re.compile(r'\s*\(.*?\)\s*')


@dataclass
class SuspiciousMatch:
    """Спорное совпадение для проверки агентом."""
    mo_name: str          # название МО из baseMO
    org_name: str         # название организации из RMZ7KH
    sub_rf: str           # субъект из baseMO
    mun_r_name: str       # район из baseMO
    reason: str           # причина подозрения: "multiple" или "district_weak"


@dataclass
class MergeResult:
    written: int
    skipped_existing: int
    not_found: int
    suspicious: list[SuspiciousMatch]
    log_lines: list[str]


def run_merge() -> MergeResult:
    """
    Запускает слияние. Возвращает результат с количеством записей
    и списком спорных совпадений для проверки агентом.
    """
    if not BASE_MO_PATH.exists():
        raise FileNotFoundError("Файл base.xlsx не загружен")
    if not RMZ_PATH.exists():
        raise FileNotFoundError("Файл RMZ7KH.xlsx не загружен")

    logger.info("rmz_merge_start")

    # Читаем baseMO — только первый лист
    wb_base = openpyxl.load_workbook(BASE_MO_PATH, data_only=True)
    sheet_base = wb_base.worksheets[0]

    # Читаем RMZ7KH — только первый лист
    wb_rmz = openpyxl.load_workbook(RMZ_PATH, data_only=True)
    sheet_rmz = wb_rmz.worksheets[0]

    # Собираем уже обработанные МО из data.xlsx
    existing_mun_names = _get_existing_mun_names()

    # Загружаем строки baseMO
    base_rows = _load_base_rows(sheet_base)
    logger.info("rmz_merge_base_loaded", total=len(base_rows))

    # Загружаем RMZ7KH — фильтруем только администрации
    rmz_rows = _load_rmz_rows(sheet_rmz)
    logger.info("rmz_merge_rmz_loaded", total=len(rmz_rows))

    # Определяем стартовый ID
    writer = ExcelWriter(DATA_XLSX_PATH)
    start_id = _get_next_id()

    written = 0
    skipped_existing = 0
    not_found = 0
    suspicious: list[SuspiciousMatch] = []
    log_lines: list[str] = []
    record_id = start_id

    for mo in base_rows:
        # Пропускаем уже обработанные
        if mo["mun_name"].strip().lower() in existing_mun_names:
            skipped_existing += 1
            continue

        if not mo["words"]:
            log_lines.append(f"НЕТ КЛЮЧА | МО: {mo['mun_name']} | Субъект: {mo['sub_rf']}")
            not_found += 1
            continue

        candidates, level = _find_candidates(
            mo["words"], rmz_rows, mo["sub_rf_norm"], mo["mun_r_name"]
        )

        if not candidates:
            log_lines.append(
                f"НЕ НАЙДЕНО | МО: {mo['mun_name']} | Субъект: {mo['sub_rf']} "
                f"| Район: {mo['mun_r_name']} | Причина: {level}"
            )
            not_found += 1
            continue

        match = candidates[0]

        # Фиксируем спорные совпадения для агента
        if len(candidates) > 1:
            suspicious.append(SuspiciousMatch(
                mo_name=mo["mun_name"],
                org_name=match["B"],
                sub_rf=mo["sub_rf"],
                mun_r_name=mo["mun_r_name"],
                reason=f"multiple({len(candidates)})",
            ))
            log_lines.append(
                f"ДУБЛЬ (взято 1е) | МО: {mo['mun_name']} | Кандидатов: {len(candidates)}"
            )

        email1, email_rest = _split_first(match["G"])
        phone1, phone_rest = _split_first(match["F"])

        record = MoRecord(
            id=record_id,
            sub_rf=mo["sub_rf"],
            mun_r_name=mo["mun_r_name"],
            mun_name=mo["mun_name"],
            adm_name=match["B"],           # столбец E: полное название администрации
            adres=match["L"],              # столбец F: юридический адрес
            head_fio=match["P"],           # столбец G: ФИО руководителя
            population=mo["population"],
            email_osn=email1 or "",        # первый email
            email_dop=email_rest or "",    # остальные email
            tel_osn=phone1 or "",          # первый телефон
            tel_dop=phone_rest or "",      # остальные телефоны
            requisites_inn=str(match["D"]) if match["D"] else "",    # ИНН
            requisites_kpp=str(match["E"]) if match["E"] else "",    # КПП
            requisites_ogrn=str(match["C"]) if match["C"] else "",   # ОГРН
            status="rmz",
        )
        writer.append_record(record)

        # Сохраняем каждые 100 записей
        if written > 0 and written % 100 == 0:
            writer.save()
            logger.info("rmz_merge_checkpoint", written=written)

        written += 1
        record_id += 1

    writer.save()
    writer.close()

    logger.info(
        "rmz_merge_done",
        written=written,
        skipped_existing=skipped_existing,
        not_found=not_found,
        suspicious=len(suspicious),
    )

    return MergeResult(
        written=written,
        skipped_existing=skipped_existing,
        not_found=not_found,
        suspicious=suspicious,
        log_lines=log_lines,
    )


# ------------------------------------------------------------------
# Вспомогательные функции
# ------------------------------------------------------------------

def _load_base_rows(sheet) -> list[dict]:
    rows = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        sub_rf = _clean(row[0])
        mun_r_name = _clean(row[1])
        mun_name = _clean(row[2])
        population = row[3]
        if not mun_name:
            continue
        rows.append({
            "sub_rf": sub_rf,
            "sub_rf_norm": _normalize_region(sub_rf),
            "mun_r_name": mun_r_name,
            "mun_name": mun_name,
            "population": population,
            "words": _extract_keywords(mun_name),
        })
    return rows


def _load_rmz_rows(sheet) -> list[dict]:
    """
    Загружает RMZ7KH. Столбцы:
    A=сокр. наим, B=полн. наим, C=ОГРН, D=ИНН, E=КПП,
    F=телефоны, G=email, H=сайт, I=статус, J=дата рег,
    K=регион, L=юр. адрес, M=ОКВЭД, N=осн. вид деят.,
    O=доп. вид деят., P=руководитель, Q=должность
    """
    rows = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        b = row[1] if len(row) > 1 else None
        if not b:
            continue
        b_str = _clean(str(b))
        if not _is_administration(b_str):
            continue
        region_k = row[10] if len(row) > 10 else None
        rows.append({
            "B": b_str,
            "B_lower": b_str.lower(),
            "C": row[2] if len(row) > 2 else None,   # ОГРН
            "D": row[3] if len(row) > 3 else None,   # ИНН
            "E": row[4] if len(row) > 4 else None,   # КПП
            "F": row[5] if len(row) > 5 else None,   # телефоны
            "G": row[6] if len(row) > 6 else None,   # email
            "L": row[11] if len(row) > 11 else None, # юр. адрес
            "P": row[15] if len(row) > 15 else None, # руководитель
            "region_norm": _normalize_region(str(region_k) if region_k else ""),
        })
    return rows


def _get_existing_mun_names() -> set[str]:
    """Возвращает множество названий МО уже записанных в data.xlsx."""
    if not DATA_XLSX_PATH.exists():
        return set()
    try:
        wb = openpyxl.load_workbook(DATA_XLSX_PATH, data_only=True, read_only=True)
        sheet = wb.worksheets[0]
        names = set()
        # MUN_NAME — столбец D (индекс 3), данные начинаются с 3й строки
        for row in sheet.iter_rows(min_row=3, values_only=True):
            if row[3]:
                names.add(str(row[3]).strip().lower())
        wb.close()
        return names
    except Exception as e:
        logger.warning("rmz_get_existing_failed", error=str(e))
        return set()


def _get_next_id() -> int:
    """Возвращает следующий доступный ID для записи."""
    if not DATA_XLSX_PATH.exists():
        return 1
    try:
        wb = openpyxl.load_workbook(DATA_XLSX_PATH, data_only=True, read_only=True)
        sheet = wb.worksheets[0]
        last_id = 0
        for row in sheet.iter_rows(min_row=3, values_only=True):
            val = row[0]
            if val is not None:
                try:
                    last_id = max(last_id, int(val))
                except (ValueError, TypeError):
                    pass
        wb.close()
        return last_id + 1
    except Exception:
        return 1


def _find_candidates(words: list, rmz_rows: list, subj_norm: set, district: str) -> tuple:
    """
    Поиск кандидатов в RMZ7KH.
    Шаг 1: субъект + все ключевые слова МО
    Шаг 2: фильтр по району (обязателен)
    """
    def matches_all(b_lower: str) -> bool:
        for w in words:
            variants = _get_variants(w)
            if not any(v in b_lower for v in variants):
                return False
        return True

    level1 = [
        r for r in rmz_rows
        if _regions_match(subj_norm, r["region_norm"]) and matches_all(r["B_lower"])
    ]

    if not level1:
        return [], "not_found"

    if district:
        filtered = [r for r in level1 if _district_match(district, r["B"])]
        if not filtered:
            return [], "district_mismatch"
        if len(filtered) == 1:
            return filtered, "exact"
        return filtered, f"multiple({len(filtered)})"

    if len(level1) == 1:
        return level1, "exact_no_district"
    return level1, f"multiple_no_district({len(level1)})"


def _is_administration(name: str) -> bool:
    lower = name.lower()
    return (
        any(m in lower for m in ALLOWED_MARKERS)
        and not any(m in lower for m in BANNED_MARKERS)
    )


def _extract_keywords(name: str) -> list[str]:
    s = BRACKET_RE.sub(' ', name).strip().lower()
    return [w for w in s.split() if w not in NOISE_WORDS and len(w) > 2]


def _normalize_region(text: str) -> set[str]:
    if not text:
        return set()
    s = text.lower().replace(",", " ").replace("-", " ").replace(".", " ")
    return {w for w in s.split() if w not in REGION_NOISE and len(w) > 2}


def _regions_match(a: set, b: set) -> bool:
    return bool(a and b and a & b)


def _district_match(district_from_mo: str, org_name: str) -> bool:
    prefix = _get_district_prefix(district_from_mo)
    if not prefix:
        return False
    # Ищем слово перед "муниципального района"
    lower = org_name.lower()
    marker = "муниципального района"
    idx = lower.find(marker)
    if idx != -1:
        before = lower[:idx].strip().split()
        if before:
            return before[-1].startswith(prefix)
    # Fallback: ищем префикс где угодно в строке
    return prefix in lower


def _get_district_prefix(district: str) -> str:
    if not district:
        return ""
    s = district.lower()
    for noise in DISTRICT_NOISE:
        s = s.replace(noise, " ")
    words = s.split()
    if not words:
        return ""
    w = words[0]
    return w[:10] if len(w) > 13 else w[:7]


def _get_variants(word: str) -> list[str]:
    root = word[:10] if len(word) > 13 else word[:7]
    variants = [root]
    special = {
        "новый": "ново", "нижний": "нижне", "верхний": "верхне",
        "старый": "старо", "большой": "больше",
    }
    if word in special:
        variants.append(special[word])
    return variants


def _split_first(value) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    s = str(value).strip()
    if "," in s:
        idx = s.index(",")
        return s[:idx].strip() or None, s[idx + 1:].strip() or None
    return s or None, None


def _clean(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()