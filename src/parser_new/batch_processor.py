"""
batch_processor.py — детерминированный скрипт для заполнения данных по МО.

Работает БЕЗ LLM в основном цикле:
  1. Читает Excel — находит незаполненные строки
  2. Для каждой строки: Tavily → rusprofile → ИНН → Checko → данные
  3. Дополняет только пустые поля
  4. Сохраняет прогресс после каждой строки

Запуск: python batch_processor.py --file путь/к/файлу.xlsx
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from tavily import TavilyClient

sys.path.insert(0, str(Path(__file__).parent))
from src.parser_new import config
from src.parser_new.logger import logger


# ==============================
# КОНСТАНТЫ
# ==============================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Колонки файла (индексы 1-based)
COL = {
    "ID":               1,
    "SUB_RF":           2,
    "MUN_R_NAME":       3,
    "MUN_NAME":         4,
    "ADM_NAME":         5,
    "ADRES":            6,
    "HEAD_FIO":         7,
    "POPULATION":       8,
    "EMAIL_OSN":        9,
    "EMAIL_DOP":        10,
    "TEL_OSN":          11,
    "TEL_DOP":          12,
    "REQUISITES_INN":   13,
    "REQUISITES_KPP":   14,
    "REQUISITES_OGRN":  15,
    "REQUISITES_OKPO":  16,
    "REQUISITES_OKTMO": 17,
    "STATUS":           18,
    "NOTE":             19,
}

DATA_START_ROW = 3  # данные начинаются с 3-й строки (шапка 2 строки)


# ==============================
# СТРУКТУРА ДАННЫХ
# ==============================

@dataclass
class MORecord:
    """Данные одной строки из Excel."""
    excel_row:    int
    sub_rf:       str = ""
    mun_r_name:   str = ""
    mun_name:     str = ""
    adm_name:     str = ""
    adres:        str = ""
    head_fio:     str = ""
    population:   str = ""
    email_osn:    str = ""
    email_dop:    str = ""
    tel_osn:      str = ""
    tel_dop:      str = ""
    inn:          str = ""
    kpp:          str = ""
    ogrn:         str = ""
    okpo:         str = ""
    oktmo:        str = ""
    status:       str = ""
    note:         str = ""

    def missing_fields(self) -> list[str]:
        """Возвращает список незаполненных полей."""
        checks = {
            "ADM_NAME":         self.adm_name,
            "ADRES":            self.adres,
            "HEAD_FIO":         self.head_fio,
            "EMAIL_OSN":        self.email_osn,
            "TEL_OSN":          self.tel_osn,
            "REQUISITES_INN":   self.inn,
            "REQUISITES_KPP":   self.kpp,
            "REQUISITES_OGRN":  self.ogrn,
            "REQUISITES_OKPO":  self.okpo,
            "REQUISITES_OKTMO": self.oktmo,
        }
        return [k for k, v in checks.items() if not v]

    def is_complete(self) -> bool:
        return len(self.missing_fields()) == 0

    def needs_processing(self) -> bool:
        """Строка требует обработки если есть незаполненные поля."""
        return bool(self.mun_name or self.adm_name) and not self.is_complete()


# ==============================
# ЧТЕНИЕ EXCEL
# ==============================

def read_excel(file_path: str) -> tuple[object, list[MORecord]]:
    """Читает Excel и возвращает (workbook, список строк требующих обработки)."""
    wb = load_workbook(file_path)
    ws = wb.active

    records = []
    for row_idx in range(DATA_START_ROW, ws.max_row + 1):
        def v(col_name: str) -> str:
            val = ws.cell(row_idx, COL[col_name]).value
            return str(val).strip() if val else ""

        rec = MORecord(
            excel_row   = row_idx,
            sub_rf      = v("SUB_RF"),
            mun_r_name  = v("MUN_R_NAME"),
            mun_name    = v("MUN_NAME"),
            adm_name    = v("ADM_NAME"),
            adres       = v("ADRES"),
            head_fio    = v("HEAD_FIO"),
            population  = v("POPULATION"),
            email_osn   = v("EMAIL_OSN"),
            email_dop   = v("EMAIL_DOP"),
            tel_osn     = v("TEL_OSN"),
            tel_dop     = v("TEL_DOP"),
            inn         = v("REQUISITES_INN"),
            kpp         = v("REQUISITES_KPP"),
            ogrn        = v("REQUISITES_OGRN"),
            okpo        = v("REQUISITES_OKPO"),
            oktmo       = v("REQUISITES_OKTMO"),
            status      = v("STATUS"),
            note        = v("NOTE"),
        )

        if rec.needs_processing():
            records.append(rec)

    logger.info(f"Найдено строк для обработки: {len(records)}")
    return wb, records


# ==============================
# ЗАПИСЬ В EXCEL
# ==============================

def write_record(ws, rec: MORecord) -> None:
    """Записывает данные записи в Excel — только пустые ячейки."""
    updates = {
        "ADM_NAME":         rec.adm_name,
        "ADRES":            rec.adres,
        "HEAD_FIO":         rec.head_fio,
        "EMAIL_OSN":        rec.email_osn,
        "EMAIL_DOP":        rec.email_dop,
        "TEL_OSN":          rec.tel_osn,
        "TEL_DOP":          rec.tel_dop,
        "REQUISITES_INN":   rec.inn,
        "REQUISITES_KPP":   rec.kpp,
        "REQUISITES_OGRN":  rec.ogrn,
        "REQUISITES_OKPO":  rec.okpo,
        "REQUISITES_OKTMO": rec.oktmo,
        "NOTE":             rec.note,
    }
    for col_name, value in updates.items():
        if value:  # не перезаписываем пустыми значениями
            existing = ws.cell(rec.excel_row, COL[col_name]).value
            if not existing:  # только если ячейка пустая
                ws.cell(rec.excel_row, COL[col_name], value)


# ==============================
# ПОИСК НА RUSPROFILE
# ==============================

def search_rusprofile(rec: MORecord) -> tuple[str | None, bool]:
    """
    Ищет организацию на rusprofile через Tavily.
    Возвращает (ИНН, ликвидирована).
    """
    if not config.TAVILY_API_KEY:
        return None, False

    try:
        client = TavilyClient(config.TAVILY_API_KEY)
        query = f"{rec.sub_rf} {rec.mun_r_name} {rec.mun_name} администрация"

        response = client.search(
            query=query,
            search_depth="advanced",
            include_domains=["rusprofile.ru"],
            max_results=3,
        )

        results = response.get("results", [])
        if not results:
            return None, False

        # Берём лучший результат с /id/ ПРОВЕРЯЯ что это орган власти
        def is_valid_admin(title: str) -> bool:
            t = title.lower()
            bad = ["потребительск", "общество", "колхоз", "совхоз",
                   "культур", "досуг", "библиотек", "музей", "школ",
                   "больниц", "спорт", "казначейств", "налогов",
                   "пенсион", "соцзащит", "мфц", "водоканал", "жкх"]
            if any(k in t for k in bad):
                return False
            good = ["администрац", "сельсовет", "поселени", "мэри",
                    "управ", "исполком", "округ", "сельского"]
            return any(k in t for k in good)

        # Извлекаем ключевые слова из названия МО (имя села/сельсовета)
        mun_keywords = _extract_key_words(rec.mun_name)

        # Собираем все подходящие результаты, потом выберем лучший
        candidates = []
        for r in sorted(results, key=lambda x: x.get("score", 0), reverse=True):
            if "/id/" not in r.get("url", ""):
                continue
            title = r.get("title", "")
            content_text = r.get("content", "")
            combined = (title + " " + content_text).lower()

            if not is_valid_admin(title):
                continue

            # Проверка соответствия МО — по корням слов
            if mun_keywords:
                ok = False
                for kw in mun_keywords:
                    root = kw[:max(5, len(kw) - 3)]
                    if root in combined:
                        ok = True
                        break
                if not ok:
                    continue

            candidates.append(r)

        if not candidates:
            return None, False

        # Приоритет: сначала "АДМИНИСТРАЦИЯ", потом всё остальное
        admin_first = [c for c in candidates if "администрац" in c.get("title", "").lower()]
        others = [c for c in candidates if "администрац" not in c.get("title", "").lower()]
        best = (admin_first + others)[0]

        snippet = best.get("content", "").lower()
        title   = best.get("title", "")

        # Проверяем статус из сниппета
        liquidated = bool(
            re.search(r"статус[:\s]+ликвидирован", snippet) or
            re.search(r"организация ликвидирована", snippet) or
            re.search(r"ликвидирована с \d{2}\.\d{2}\.\d{4}", snippet)
        )

        if liquidated:
            return None, True

        # Извлекаем ИНН организации из сниппета
        # ВАЖНО: ИНН руководителя — 12 цифр, ИНН организации — 10 цифр
        # Ищем именно по контексту "Организации присвоен ИНН" или "ИНН организации"
        content_text = best.get("content", "")

        # Сначала ищем по контексту
        inn = None
        patterns = [
            r"[Оо]рганизации присвоен ИНН[\s]*(\d{10})\b",
            r"[Оо]рганизации[^.]*?ИНН[\s/:]+(\d{10})\b",
            r"присвоен ИНН[\s]*(\d{10})\b",
        ]
        for pat in patterns:
            m = re.search(pat, content_text)
            if m:
                inn = m.group(1)
                break

        # Если контекст не сработал — берём ВСЕ 10-значные ИНН
        # и фильтруем 12-значные (это ИНН людей)
        if not inn:
            # Заменяем 12-значные числа на пустоту чтобы они не мешали
            clean_text = re.sub(r"\b\d{12}\b", "", content_text)
            m = re.search(r"ИНН[\s/:]+(\d{10})\b", clean_text)
            if m:
                inn = m.group(1)

        if inn:
            return inn, False

        # Если ИНН не в сниппете — заходим на страницу
        url = best["url"]
        resp = httpx.get(url, headers=HEADERS, timeout=10, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")
        for row in soup.select(".company-row"):
            txt = row.get_text()
            if "ИНН" in txt and "КПП" in txt:  # блок реквизитов организации
                m = re.search(r"\b(\d{10})\b", txt)
                if m:
                    return m.group(), False

        return None, False

    except Exception as e:
        logger.warning(f"[rusprofile] Ошибка для {rec.mun_name}: {e}")
        return None, False


# ==============================
# ЗАПРОС К CHECKO
# ==============================

_checko_cache: dict[str, dict] = {}


def fetch_checko_by_inn(inn: str) -> dict | None:
    """Получает данные организации из Checko по ИНН."""
    if inn in _checko_cache:
        return _checko_cache[inn]

    if not config.CHECKO_API_KEY:
        return None

    try:
        resp = httpx.get(
            f"https://api.checko.ru/v2/company",
            params={"key": config.CHECKO_API_KEY, "inn": inn},
            timeout=15,
        )
        data = resp.json()

        if data.get("meta", {}).get("status") == "error":
            return None

        org = data.get("data", {})
        if not org:
            return None

        # Парсим нужные поля
        contacts = org.get("Контакты", {}) or {}
        phones   = contacts.get("Тел", []) or []
        emails   = contacts.get("Емэйл", []) or []

        oktmo = org.get("ОКТМО", {}) or {}

        adres = org.get("ЮрАдрес", "")
        if isinstance(adres, dict):
            adres = adres.get("АдресРФ", "") or str(adres)

        # Руководитель
        head_fio = ""
        for r in (org.get("Руковод", []) or []):
            if not r.get("Недост") and not r.get("ДисквЛицо"):
                head_fio = r.get("ФИО", "")
                break

        result = {
            "adm_name": org.get("НаимПолн", ""),
            "adres":    adres,
            "head_fio": head_fio,
            "email_osn": emails[0] if emails else "",
            "email_dop": ", ".join(emails[1:]) if len(emails) > 1 else "",
            "tel_osn":   phones[0] if phones else "",
            "tel_dop":   ", ".join(phones[1:]) if len(phones) > 1 else "",
            "inn":       org.get("ИНН", inn),
            "kpp":       org.get("КПП", ""),
            "ogrn":      org.get("ОГРН", ""),
            "okpo":      org.get("ОКПО", ""),
            "oktmo":     oktmo.get("Код", ""),
        }
        _checko_cache[inn] = result
        return result

    except Exception as e:
        logger.warning(f"[checko] Ошибка для ИНН {inn}: {e}")
        return None


def search_checko_by_name(rec: MORecord) -> str | None:
    """Ищет ИНН в Checko по названию МО."""
    from src.parser_new.tools.regions import get_region_code

    region_code = get_region_code(rec.sub_rf)
    if not region_code:
        return None

    try:
        resp = httpx.get(
            "https://api.checko.ru/v2/search",
            params={
                "key":    config.CHECKO_API_KEY,
                "by":     "name",
                "obj":    "org",
                "query":  rec.mun_name,
                "region": region_code,
            },
            timeout=15,
        )
        data = resp.json()
        records = data.get("data", {}).get("Записи", []) or []

        admin_keywords = ["администрац", "сельсовет", "поселени"]
        district_key = rec.mun_r_name.lower().replace("муниципальный район", "").strip()
        district_words = [w for w in district_key.split() if len(w) > 3]

        for r in records:
            status = (r.get("Статус", "") or "").lower()
            if "не действует" in status or "ликвид" in status:
                continue
            name = (r.get("НаимПолн", "") or r.get("НаимСокр", "") or "").lower()
            if not any(k in name for k in admin_keywords):
                continue
            if district_words and not any(w in name for w in district_words):
                continue
            return r.get("ИНН")

        return None

    except Exception as e:
        logger.warning(f"[checko/search] Ошибка для {rec.mun_name}: {e}")
        return None


# ==============================
# ПОИСК КОНТАКТОВ НА САЙТЕ
# ==============================

def search_contacts_online(adm_name: str) -> dict:
    """Ищет контакты администрации на официальном сайте."""
    if not config.TAVILY_API_KEY or not adm_name:
        return {}

    try:
        client = TavilyClient(config.TAVILY_API_KEY)
        response = client.search(
            query=f"{adm_name} официальный сайт",
            search_depth="basic",
            exclude_domains=["rusprofile.ru", "egrul.ru", "checko.ru",
                             "sbis.ru", "list-org.com"],
            max_results=3,
        )

        results = response.get("results", [])
        if not results:
            return {}

        # Парсим первый результат
        url = results[0]["url"]
        resp = httpx.get(url, headers=HEADERS, timeout=10, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        text = soup.get_text()
        phones = re.findall(
            r'(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}',
            text
        )
        emails = re.findall(
            r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
            text
        )

        return {
            "tel_osn":   phones[0] if phones else "",
            "tel_dop":   phones[1] if len(phones) > 1 else "",
            "email_osn": emails[0] if emails else "",
            "email_dop": ", ".join(emails[1:3]) if len(emails) > 1 else "",
        }

    except Exception:
        return {}


# ==============================
# ВАЛИДАЦИЯ СООТВЕТСТВИЯ ОРГАНИЗАЦИИ
# ==============================

def _extract_key_words(text: str) -> list[str]:
    """Извлекает ключевые слова из названия МО или района."""
    text = text.lower()
    # Убираем общие слова
    stop_words = [
        "сельское поселение", "городское поселение", "муниципальный район",
        "муниципальное образование", "сельсовет", "посёлок", "поселок",
        "село", "город", "район", "поселение", "администрация", "мо",
        "республика", "край", "область", "округ"
    ]
    for sw in stop_words:
        text = text.replace(sw, " ")
    # Берём слова длиннее 3 символов
    words = [w.strip() for w in text.split() if len(w.strip()) > 3]
    return words


def validate_org_matches(org_name: str, rec: MORecord) -> bool:
    """
    Проверяет что найденная организация соответствует МО.
    Сравнивает по корням слов (учитывает склонения).
    Например: "Челмужское" и "Челмужского" — одно и то же.
    """
    if not org_name:
        return False

    org_lower = org_name.lower()
    mun_keywords = _extract_key_words(rec.mun_name)

    if not mun_keywords:
        return True  # нет ключевых слов для проверки — пропускаем

    # Сравниваем по корням — берём первые 5-6 букв слова
    for kw in mun_keywords:
        # Корень слова — без окончаний
        root = kw[:max(5, len(kw) - 3)]
        if root in org_lower:
            return True

    return False


# ==============================
# ОСНОВНАЯ ЛОГИКА ОБРАБОТКИ
# ==============================

def process_record(rec: MORecord) -> MORecord:
    """
    Обрабатывает одну строку — заполняет пустые поля.
    Не трогает уже заполненные.
    """
    # Шаг 1: ищем ИНН если нет
    inn = rec.inn
    inn_from_user = bool(inn)  # ИНН был в файле или мы его нашли?

    if not inn:
        # Сначала rusprofile
        found_inn, liquidated = search_rusprofile(rec)

        if liquidated:
            if not rec.adm_name:
                rec.adm_name = "ликвидирована"
            rec.note = "ликвидирована"
            return rec

        if found_inn:
            inn = found_inn
        else:
            # Fallback: Checko поиск по названию
            inn = search_checko_by_name(rec)

        if not inn:
            rec.note = "не найдено"
            return rec

    # Шаг 2: получаем данные из Checko по ИНН
    checko_data = fetch_checko_by_inn(inn)
    if not checko_data:
        rec.note = "ИНН найден но данные Checko недоступны"
        rec.inn = inn
        return rec

    # Валидация только если ИНН был найден поиском (не от пользователя)
    if not inn_from_user:
        found_name = checko_data.get("adm_name", "")
        if not validate_org_matches(found_name, rec):
            logger.warning(
                f"Несоответствие: искали '{rec.mun_name}', нашли '{found_name}'"
            )
            rec.note = f"проверить вручную: нашли {found_name[:80]}"
            return rec

    # Заполняем только пустые поля
    if not rec.inn:          rec.inn      = checko_data["inn"]
    if not rec.adm_name:     rec.adm_name = checko_data["adm_name"]
    if not rec.adres:        rec.adres    = checko_data["adres"]
    if not rec.head_fio:     rec.head_fio = checko_data["head_fio"]
    if not rec.email_osn:    rec.email_osn = checko_data["email_osn"]
    if not rec.email_dop:    rec.email_dop = checko_data["email_dop"]
    if not rec.tel_osn:      rec.tel_osn  = checko_data["tel_osn"]
    if not rec.tel_dop:      rec.tel_dop  = checko_data["tel_dop"]
    if not rec.kpp:          rec.kpp      = checko_data["kpp"]
    if not rec.ogrn:         rec.ogrn     = checko_data["ogrn"]
    if not rec.okpo:         rec.okpo     = checko_data["okpo"]
    if not rec.oktmo:        rec.oktmo    = checko_data["oktmo"]

    # Шаг 3: если нет контактов — ищем на официальном сайте
    if not rec.email_osn or not rec.tel_osn:
        name_for_search = rec.adm_name or checko_data.get("adm_name", "")
        if name_for_search:
            contacts = search_contacts_online(name_for_search)
            if not rec.email_osn and contacts.get("email_osn"):
                rec.email_osn = contacts["email_osn"]
            if not rec.email_dop and contacts.get("email_dop"):
                rec.email_dop = contacts["email_dop"]
            if not rec.tel_osn and contacts.get("tel_osn"):
                rec.tel_osn = contacts["tel_osn"]
            if not rec.tel_dop and contacts.get("tel_dop"):
                rec.tel_dop = contacts["tel_dop"]

    return rec


def _copy_row_to_failed(ws_src, ws_dst, src_row: int, dst_row: int) -> None:
    """Копирует строку из основного файла в файл непроверенных."""
    for col_idx in range(1, len(COL) + 1):
        value = ws_src.cell(src_row, col_idx).value
        if value is not None:
            ws_dst.cell(dst_row, col_idx, value)


# ==============================
# ГЛАВНАЯ ФУНКЦИЯ
# ==============================

def run(file_path: str, save_every: int = 10) -> None:
    """
    Основной цикл обработки.

    Args:
        file_path:  путь к Excel файлу
        save_every: сохранять файл каждые N строк
    """
    path = Path(file_path)
    if not path.exists():
        logger.error(f"Файл не найден: {file_path}")
        return

    # Создаём копию для работы — не трогаем оригинал
    out_dir = Path(config.OUTPUT_DIR) / "latest"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = out_dir / f"batch_{timestamp}.xlsx"
    failed_path = out_dir / f"batch_FAILED_{timestamp}.xlsx"

    import shutil
    shutil.copy2(str(path), str(out_path))
    # Создаём пустой файл для непроверенных строк со структурой как у оригинала
    shutil.copy2(str(path), str(failed_path))
    logger.info(f"Рабочая копия: {out_path}")
    logger.info(f"Файл для непроверенных: {failed_path}")

    wb, records = read_excel(str(out_path))
    ws = wb.active

    # Открываем файл для непроверенных и удаляем все строки данных
    wb_failed = load_workbook(str(failed_path))
    ws_failed = wb_failed.active
    if ws_failed.max_row >= DATA_START_ROW:
        ws_failed.delete_rows(DATA_START_ROW, ws_failed.max_row)
    failed_row_idx = DATA_START_ROW  # текущая строка в файле непроверенных

    total     = len(records)
    processed = 0
    found     = 0
    not_found = 0
    liquidated_count = 0

    logger.info(f"Начинаю обработку {total} строк...")
    print(f"\n{'='*50}")
    print(f"Найдено строк для обработки: {total}")
    print(f"Файл результата: {out_path}")
    print(f"{'='*50}\n")

    for i, rec in enumerate(records, 1):
        try:
            print(f"[{i}/{total}] {rec.sub_rf} | {rec.mun_name[:40]}...", end=" ", flush=True)

            updated = process_record(rec)
            write_record(ws, updated)

            # Если строка не нашлась или требует проверки — копируем в failed файл
            is_failed = (
                updated.note in ("не найдено", "ликвидирована")
                or "проверить вручную" in updated.note
                or "недоступны" in updated.note
            )

            if updated.note == "ликвидирована":
                liquidated_count += 1
                print("⚠️  ликвидирована")
            elif updated.note == "не найдено":
                not_found += 1
                print("❌ не найдено")
                _copy_row_to_failed(ws, ws_failed, updated.excel_row, failed_row_idx)
                failed_row_idx += 1
            elif "проверить вручную" in updated.note:
                not_found += 1
                print(f"⚠️  {updated.note[:60]}")
                _copy_row_to_failed(ws, ws_failed, updated.excel_row, failed_row_idx)
                failed_row_idx += 1
            elif "недоступны" in updated.note:
                not_found += 1
                print(f"⚠️  {updated.note[:60]}")
                _copy_row_to_failed(ws, ws_failed, updated.excel_row, failed_row_idx)
                failed_row_idx += 1
            else:
                found += 1
                print(f"✅ ИНН: {updated.inn}")

            processed += 1

            # Сохраняем прогресс
            if i % save_every == 0:
                wb.save(str(out_path))
                wb_failed.save(str(failed_path))
                logger.info(f"Прогресс сохранён: {i}/{total}")

            # Небольшая задержка чтобы не долбить API
            time.sleep(0.5)

        except KeyboardInterrupt:
            logger.info("Прерывание пользователем — сохраняю прогресс...")
            wb.save(str(out_path))
            wb_failed.save(str(failed_path))
            print(f"\n\nПрервано. Обработано {processed}/{total}. Файл сохранён.")
            return

        except Exception as e:
            logger.error(f"Ошибка для строки {rec.excel_row}: {e}")
            ws.cell(rec.excel_row, COL["NOTE"], f"ошибка: {str(e)[:50]}")
            not_found += 1
            print(f"❌ ошибка: {e}")

    # Финальное сохранение
    wb.save(str(out_path))
    wb_failed.save(str(failed_path))

    # Архивная копия
    arc_dir = Path(config.OUTPUT_DIR) / "archive" / datetime.now().strftime("%Y-%m-%d")
    arc_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(out_path), str(arc_dir / out_path.name))
    shutil.copy2(str(failed_path), str(arc_dir / failed_path.name))

    print(f"\n{'='*50}")
    print(f"✅ Готово!")
    print(f"   Обработано:    {processed}")
    print(f"   Найдено:       {found}")
    print(f"   Ликвидировано: {liquidated_count}")
    print(f"   Не найдено:    {not_found}")
    print(f"   Основной файл: {out_path}")
    print(f"   Непроверенные: {failed_path}")
    print(f"{'='*50}\n")


# ==============================
# ТОЧКА ВХОДА
# ==============================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Пакетная обработка МО")
    parser.add_argument("--file", required=True, help="Путь к Excel файлу")
    parser.add_argument("--save-every", type=int, default=10,
                        help="Сохранять каждые N строк (по умолчанию 10)")
    args = parser.parse_args()

    run(args.file, args.save_every)