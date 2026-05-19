"""
tools/checko_tool.py — инструменты для работы с API Checko.

Два запроса:
  1. /company — получить полные данные организации по ИНН
  2. /search  — найти организацию по названию/руководителю/ОКВЭД

Для поиска администраций МО используется поиск по названию (by=name)
с фильтрацией по ОКВЭД органов власти.
"""
from __future__ import annotations

import httpx
from langchain.tools import tool
from tenacity import retry, stop_after_attempt, wait_fixed

from src.parser_new import config
from src.parser_new.logger import logger
from src.parser_new.tools.regions import get_region_code
from src.parser_new.memory.cache_memory import org_cache_get, org_cache_set

# ==============================
# КОНСТАНТЫ
# ==============================

CHECKO_BASE = "https://api.checko.ru/v2"

# ОКВЭД органов местного самоуправления
# Текстовые значения ОКВЭД органов МСУ (как возвращает Checko)
ADMIN_OKVED_TEXT = [
    "деятельность органов местного самоуправления сельских поселений",
    "деятельность органов местного самоуправления городских поселений",
    "деятельность органов местного самоуправления муниципальных районов",
    "деятельность органов местного самоуправления городских округов",
    "деятельность органов местного самоуправления",
    "государственное управление общего характера",
]


# ==============================
# НИЗКОУРОВНЕВЫЕ ЗАПРОСЫ
# ==============================

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _checko_get(endpoint: str, params: dict) -> dict:
    """Базовый GET запрос к Checko API."""
    response = httpx.get(
        f"{CHECKO_BASE}/{endpoint}",
        params={"key": config.CHECKO_API_KEY, **params},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


# ==============================
# ПАРСИНГ ОТВЕТА /company
# ==============================

def _parse_company(data: dict, extra_fields: list[str] | None = None) -> dict:
    """
    Извлекает нужные поля из ответа /company.
    extra_fields — дополнительные поля по запросу пользователя.
    """
    result = {}

    # Реквизиты
    result["ОГРН"]            = data.get("ОГРН", "")
    result["ИНН"]             = data.get("ИНН", "")
    result["КПП"]             = data.get("КПП", "")
    result["ОКПО"]            = data.get("ОКПО", "")
    result["НаимСокр"]        = data.get("НаимСокр", "")
    result["НаимПолн"]        = data.get("НаимПолн", "")
    result["НаимАнгл"]        = data.get("НаимАнгл", "")
    result["ДатаРег"]         = data.get("ДатаРег", "")
    result["ДатаОГРН"]        = data.get("ДатаОГРН", "")
    result["ЮрАдрес"]         = data.get("ЮрАдрес", "")
    result["Статус"]          = data.get("Статус", "")
    result["ОКВЭД"]           = data.get("ОКВЭД", "")

    # ОКТМО
    oktmo = data.get("ОКТМО", {}) or {}
    result["ОКТМО_Код"]  = oktmo.get("Код", "")
    result["ОКТМО_Наим"] = oktmo.get("Наим", "")

    # Контакты
    contacts = data.get("Контакты", {}) or {}
    phones   = contacts.get("Тел", []) or []
    emails   = contacts.get("Емэйл", []) or []
    website  = contacts.get("ВебСайт", "") or ""

    result["Телефоны"]  = phones
    result["Емэйл"]     = emails
    result["ВебСайт"]   = website

    # Руководитель — берём первого активного
    rukovod_list = data.get("Руковод", []) or []
    head_fio = ""
    head_post = ""
    for r in rukovod_list:
        if not r.get("Недост") and not r.get("ДисквЛицо"):
            head_fio  = r.get("ФИО", "")
            head_post = r.get("НаимДолжн", "") or r.get("ВидДолжн", "")
            break
    result["ГлаваФИО"]       = head_fio
    result["ГлаваДолжность"] = head_post

    # Дополнительные поля по запросу
    if extra_fields:
        for field_name in extra_fields:
            if field_name not in result and field_name in data:
                result[field_name] = data[field_name]

    return result


def _is_admin_org(record: dict) -> bool:
    """Проверяет является ли организация органом местного самоуправления."""
    okved = (record.get("ОКВЭД", "") or "").lower()
    name  = (record.get("НаимПолн", "") or record.get("НаимСокр", "") or "").lower()

    # Проверка по текстовому значению ОКВЭД
    for okved_text in ADMIN_OKVED_TEXT:
        if okved_text in okved:
            return True

    # Дополнительная проверка по названию
    admin_keywords = ["администрация", "мэрия", "управа", "исполком",
                      "муниципальн", "поселени", "сельсовет", "горсовет"]
    return any(kw in name for kw in admin_keywords)


# ==============================
# ИНСТРУМЕНТЫ ДЛЯ АГЕНТА
# ==============================

@tool
def checko_company_tool(inn: str, extra_fields: str = "") -> str:
    """
    Получает полные данные об организации по ИНН через Checko API.
    Возвращает реквизиты, контакты, руководителя и ОКТМО.
    Используй когда известен ИНН организации.

    Параметры:
      inn:          ИНН организации (10 цифр для юрлица)
      extra_fields: дополнительные поля через запятую если нужны
                    (например "УстКап,ЧислРаб,Учредит")
    """
    if not config.CHECKO_API_KEY:
        return "CHECKO_API_KEY не задан в .env"

    try:
        logger.info(f"[checko/company] ИНН: {inn}")

        # Проверяем кэш — если уже запрашивали эту организацию в этой сессии
        cached = org_cache_get(inn)
        if cached:
            logger.info(f"[checko/company] Данные из кэша: ИНН {inn}")
            parsed = cached
            phones = parsed['Телефоны']
            emails = parsed['Емэйл']
            import json as _json
            result_data = {
                "ADM_NAME":         parsed['НаимПолн'],
                "ADRES":            parsed['ЮрАдрес'],
                "HEAD_FIO":         parsed['ГлаваФИО'],
                "EMAIL_OSN":        emails[0] if emails else "",
                "EMAIL_DOP":        ", ".join(emails[1:]) if len(emails) > 1 else "",
                "TEL_OSN":          phones[0] if phones else "",
                "TEL_DOP":          ", ".join(phones[1:]) if len(phones) > 1 else "",
                "REQUISITES_INN":   parsed['ИНН'],
                "REQUISITES_KPP":   parsed['КПП'],
                "REQUISITES_OGRN":  parsed['ОГРН'],
                "REQUISITES_OKPO":  parsed['ОКПО'],
                "REQUISITES_OKTMO": parsed['ОКТМО_Код'],
                "WEBSITE":          parsed['ВебСайт'],
            }
            return f"CHECKO_CACHE|{_json.dumps(result_data, ensure_ascii=False)}"

        response = _checko_get("company", {"inn": inn.strip()})

        meta = response.get("meta", {})
        if meta.get("status") == "error":
            return f"Checko API ошибка: {meta.get('message', 'неизвестная ошибка')}"

        data = response.get("data", {})
        if not data:
            return f"Организация с ИНН {inn} не найдена в Checko"

        extras = [f.strip() for f in extra_fields.split(",")] if extra_fields else []
        parsed = _parse_company(data, extras)

        # Сохраняем в кэш сессии
        org_cache_set(inn, parsed)

        # Возвращаем компактный формат для экономии токенов
        phones = parsed['Телефоны']
        emails = parsed['Емэйл']
        result_data = {
            "ADM_NAME":          parsed['НаимПолн'],
            "ADRES":             parsed['ЮрАдрес'],
            "HEAD_FIO":          parsed['ГлаваФИО'],
            "EMAIL_OSN":         emails[0] if emails else "",
            "EMAIL_DOP":         ", ".join(emails[1:]) if len(emails) > 1 else "",
            "TEL_OSN":           phones[0] if phones else "",
            "TEL_DOP":           ", ".join(phones[1:]) if len(phones) > 1 else "",
            "REQUISITES_INN":    parsed['ИНН'],
            "REQUISITES_KPP":    parsed['КПП'],
            "REQUISITES_OGRN":   parsed['ОГРН'],
            "REQUISITES_OKPO":   parsed['ОКПО'],
            "REQUISITES_OKTMO":  parsed['ОКТМО_Код'],
            "WEBSITE":           parsed['ВебСайт'],
            "СТАТУС":            parsed['Статус'],
        }
        import json as _json
        return f"CHECKO_OK|{_json.dumps(result_data, ensure_ascii=False)}"

    except httpx.TimeoutException:
        return f"Таймаут запроса к Checko для ИНН {inn}"
    except httpx.HTTPStatusError as e:
        return f"HTTP ошибка Checko: {e.response.status_code}"
    except Exception as e:
        logger.error(f"[checko/company] Ошибка: {e}")
        return f"Ошибка запроса к Checko: {e}"


@tool
def checko_search_tool(
    query: str,
    region: str,
    search_type: str = "name",
    only_active: bool = True,
) -> str:
    """
    Ищет организации в базе Checko.
    Используй когда нужно найти ИНН организации по её названию.
    Особенно полезно для поиска администраций муниципальных образований.

    Параметры:
      query:       название организации или МО для поиска
      region:      название региона РФ (например 'Башкортостан',
                   'Челябинская область', 'Московская обл')
      search_type: тип поиска — одно из:
                   'name'         — по названию организации (по умолчанию)
                   'leader-name'  — по ФИО руководителя
                   'founder-name' — по ФИО учредителя
                   'okved'        — по коду ОКВЭД
      only_active: True — только действующие организации (по умолчанию)
    """
    if not config.CHECKO_API_KEY:
        return "CHECKO_API_KEY не задан в .env"

    # Определяем код региона
    region_code = get_region_code(region)
    if not region_code:
        return (
            f"Не удалось определить код региона для '{region}'. "
            f"Уточни название региона."
        )

    by_map = {
        "name":          "name",
        "leader-name":   "leader-name",
        "founder-name":  "founder-name",
        "okved":         "okved",
    }
    by = by_map.get(search_type, "name")

    try:
        logger.info(f"[checko/search] '{query}' | регион {region_code} | by={by}")
        response = _checko_get("search", {
            "by":     by,
            "obj":    "org",
            "query": query,
            "region": region_code,
        })

        meta = response.get("meta", {})
        if meta.get("status") == "error":
            return f"Checko API ошибка: {meta.get('message', 'неизвестная ошибка')}"

        records = response.get("data", {}).get("Записи", []) or []
        if not records:
            return f"По запросу '{query}' в регионе '{region}' ничего не найдено в Checko"

        # Фильтр по статусу
        # Разделяем на действующие и недействующие
        active   = [r for r in records if "не действует" not in (r.get("Статус", "") or "").lower()
                    and "ликвид" not in (r.get("Статус", "") or "").lower()]
        inactive = [r for r in records if r not in active]

        if only_active and not active and inactive:
            # Все найденные ликвидированы — сообщаем об этом явно
            names = [r.get("НаимПолн") or r.get("НаимСокр", "") for r in inactive[:3]]
            return (
                f"ВНИМАНИЕ: найденные организации по запросу '{query}' ЛИКВИДИРОВАНЫ: "
                f"{'; '.join(names)}. "
                f"Необходимо найти правопреемника через rusprofile.ru"
            )

        records = active if only_active else records

        if not records:
            return f"По запросу '{query}' в регионе '{region}' ничего не найдено в Checko"

        # Для поиска администраций — приоритет организациям с подходящим ОКВЭД
        admin_records  = [r for r in records if _is_admin_org(r)]
        other_records  = [r for r in records if not _is_admin_org(r)]
        sorted_records = admin_records + other_records

        lines = [
            f"Найдено {len(records)} организаций по запросу '{query}' "
            f"в регионе '{region}' (код {region_code}):",
            ""
        ]

        for i, rec in enumerate(sorted_records[:10], 1):
            is_admin = _is_admin_org(rec)
            marker   = " [ОРГАН ВЛАСТИ]" if is_admin else ""
            lines += [
                f"{i}.{marker} {rec.get('НаимПолн') or rec.get('НаимСокр', '')}",
                f"   ИНН: {rec.get('ИНН', '')} | ОГРН: {rec.get('ОГРН', '')}",
                f"   ОКВЭД: {rec.get('ОКВЭД', '')}",
                f"   Адрес: {rec.get('ЮрАдрес', '')}",
                f"   Статус: {rec.get('Статус', '')}",
                "",
            ]

        if admin_records:
            lines.append(
                f"Рекомендую проверить организации с пометкой [ОРГАН ВЛАСТИ] "
                f"через checko_company_tool по их ИНН."
            )

        return "\n".join(lines)

    except httpx.TimeoutException:
        return "Таймаут запроса к Checko"
    except httpx.HTTPStatusError as e:
        return f"HTTP ошибка Checko: {e.response.status_code}"
    except Exception as e:
        logger.error(f"[checko/search] Ошибка: {e}")
        return f"Ошибка поиска в Checko: {e}"