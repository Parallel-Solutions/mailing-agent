"""
tools/search_tool.py — инструмент веб-поиска через Яндекс Search API.
Используется агентом когда нужно найти что-то в интернете:
официальный сайт организации, общую информацию, новости и т.д.
"""
from __future__ import annotations

import re
import httpx
from langchain.tools import tool

from src.parser_new import config
from src.parser_new.logger import logger


# ==============================
# ЯНДЕКС SEARCH API
# ==============================

_yandex_sdk = None
_yandex_search = None


def _get_yandex_search():
    global _yandex_sdk, _yandex_search
    if _yandex_search is None:
        from yandex_ai_studio_sdk import AIStudio
        _yandex_sdk = AIStudio(
            folder_id=config.YANDEX_FOLDER_ID,
            auth=config.YANDEX_API_KEY,
        )
        _yandex_search = _yandex_sdk.search_api.web("RU", groups_on_page=5)
    return _yandex_search


def _parse_yandex_xml(xml_bytes: bytes) -> list[dict]:
    """Парсит XML ответ Яндекса, возвращает список {url, title, content}."""
    try:
        xml_text = xml_bytes.decode("utf-8")
    except Exception:
        return []

    results = []
    # <doc> может иметь атрибуты (напр. <doc id="...">), поэтому <doc\b[^>]*>
    for doc in re.findall(r"<doc\b[^>]*>(.*?)</doc>", xml_text, re.DOTALL):
        url_m = re.search(r"<url>(.*?)</url>", doc, re.DOTALL)
        if not url_m:
            continue
        title_m = re.search(r"<title>(.*?)</title>", doc, re.DOTALL)
        # сниппет: <passages><passage>...</passage></passages> или <headline>
        snippet_m = re.search(r"<passages>(.*?)</passages>", doc, re.DOTALL) \
            or re.search(r"<headline>(.*?)</headline>", doc, re.DOTALL)

        url = url_m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
        content = re.sub(r"<[^>]+>", "", snippet_m.group(1)).strip() if snippet_m else ""
        results.append({"url": url, "title": title, "content": content[:500]})
    return results


def web_search(query: str, max_results: int = 5, include_domains: list[str] | None = None) -> dict:
    """
    Выполняет поиск через Яндекс и возвращает структурированный результат.

    Args:
        query:          поисковый запрос
        max_results:    сколько результатов вернуть (1–10)
        include_domains: список доменов для ограничения поиска (например ["rusprofile.ru"])

    Returns:
        {
            "success": bool,
            "results": [
                {
                    "title":   str,
                    "url":     str,
                    "content": str,  # фрагмент текста
                },
                ...
            ],
            "error": str  # только если success=False
        }
    """
    if not config.YANDEX_API_KEY or not config.YANDEX_FOLDER_ID:
        return {"success": False, "error": "YANDEX_API_KEY или YANDEX_FOLDER_ID не заданы в .env"}

    try:
        search_query = query
        if include_domains:
            domain_filter = " OR ".join(f"site:{d}" for d in include_domains)
            search_query = f"({domain_filter}) {query}"

        logger.debug(f"[search] Яндекс запрос: {search_query!r}")

        search = _get_yandex_search()
        xml_result = search.run(search_query, format="xml", page=0)
        results = _parse_yandex_xml(xml_result)[:max_results]

        logger.info(f"[search] Найдено {len(results)} результатов для: {query!r}")

        return {"success": True, "results": results}

    except Exception as e:
        logger.error(f"[search] Ошибка: {e}")
        return {"success": False, "error": str(e)}


# ==============================
# ИНСТРУМЕНТЫ ДЛЯ АГЕНТА
# ==============================

@tool
def search_tool(query: str) -> str:
    """
    Ищет информацию в интернете по запросу через Яндекс.
    Используй когда нужно:
    - найти официальный сайт организации или администрации
    - найти общую информацию о чём-либо
    - найти контакты или адрес если других источников нет
    Возвращает список найденных страниц с URL и фрагментами текста.
    """
    result = web_search(query, max_results=5)

    if not result["success"]:
        return f"Поиск не удался: {result['error']}"

    lines = ["Найденные страницы:"]
    for i, r in enumerate(result["results"], 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['content']}")
        lines.append("")

    return "\n".join(lines)


@tool
def search_deep_tool(query: str) -> str:
    """
    Расширенный поиск в интернете через Яндекс — возвращает больше результатов.
    Используй когда обычный поиск не дал нужного результата,
    или когда задача требует точных данных (официальные документы,
    реквизиты, контакты конкретной организации).
    """
    result = web_search(query, max_results=10)

    if not result["success"]:
        return f"Поиск не удался: {result['error']}"

    lines = ["Найденные страницы:"]
    for i, r in enumerate(result["results"], 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['content']}")
        lines.append("")

    return "\n".join(lines)


@tool
def search_official_site_tool(adm_name: str, fields_needed: str = "email телефон адрес глава") -> str:
    """
    Ищет официальный сайт администрации и извлекает недостающие контактные данные.
    Используй после checko_company_tool если email или телефон не найдены.
    Исключает rusprofile.ru из результатов — ищет только официальные сайты.

    Параметры:
      adm_name:      полное название администрации из столбца E
      fields_needed: какие поля ищем (например "email телефон глава")
    """
    result = web_search(f"{adm_name} официальный сайт", max_results=5)

    if not result["success"] or not result["results"]:
        return f"Официальный сайт не найден для: {adm_name}"

    # Фильтруем агрегаторы
    skip_domains = ["rusprofile.ru", "egrul.ru", "checko.ru", "sbis.ru",
                    "list-org.com", "orgpage.ru", "kartoteka.ru"]

    official_urls = [
        r for r in result["results"]
        if not any(d in r["url"] for d in skip_domains)
    ]

    if not official_urls:
        return f"Официальный сайт не найден (только агрегаторы) для: {adm_name}"

    # Парсим первый официальный сайт
    from src.parser_new.tools.scraper_tool import scrape_smart

    best_url = official_urls[0]["url"]
    scrape_result = scrape_smart(best_url)

    if not scrape_result["success"]:
        return f"Не удалось открыть сайт {best_url}: {scrape_result['error']}"

    data = scrape_result["data"]
    contacts = data.get("contacts", {})
    text = data.get("text", "")

    lines = [f"Официальный сайт: {best_url}"]

    phones = contacts.get("phones", [])
    emails = contacts.get("emails", [])

    if phones:
        lines.append(f"Телефоны: {', '.join(phones)}")
    if emails:
        lines.append(f"Email: {', '.join(emails)}")

    # Ищем главу в тексте
    head_patterns = [
        r"глава\s+[а-яё\s]+?([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)",
        r"руководитель\s+[а-яё\s]+?([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)",
    ]
    for pattern in head_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            lines.append(f"Глава: {m.group(1)}")
            break

    if len(lines) == 1:
        lines.append("Контакты на сайте не найдены")

    return "\n".join(lines)