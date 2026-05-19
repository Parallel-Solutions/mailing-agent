"""
tools/search_tool.py — инструмент веб-поиска через Tavily.
Используется агентом когда нужно найти что-то в интернете:
официальный сайт организации, общую информацию, новости и т.д.
"""
from __future__ import annotations

import httpx
from langchain.tools import tool
from tenacity import retry, stop_after_attempt, wait_fixed

from src.parser_new import config
from src.parser_new.logger import logger


# ==============================
# НИЗКОУРОВНЕВЫЙ КЛИЕНТ TAVILY
# ==============================

TAVILY_URL = "https://api.tavily.com/search"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _tavily_request(query: str, max_results: int, search_depth: str) -> dict:
    """
    Делает запрос к Tavily API.
    Отдельная функция чтобы retry работал чисто.
    """
    response = httpx.post(
        TAVILY_URL,
        json={
            "api_key":      config.TAVILY_API_KEY,
            "query":        query,
            "max_results":  max_results,
            "search_depth": search_depth,   # "basic" быстро, "advanced" глубже
            "include_answer":      True,    # Tavily сам формулирует краткий ответ
            "include_raw_content": False,   # сырой HTML не нужен
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def web_search(query: str, max_results: int = 5, deep: bool = False) -> dict:
    """
    Выполняет поиск и возвращает структурированный результат.

    Args:
        query:       поисковый запрос
        max_results: сколько результатов вернуть (1–10)
        deep:        True = глубокий поиск, медленнее но точнее

    Returns:
        {
            "success": bool,
            "answer":  str,          # краткий ответ от Tavily
            "results": [             # список найденных страниц
                {
                    "title":   str,
                    "url":     str,
                    "content": str,  # фрагмент текста
                    "score":   float # релевантность 0..1
                },
                ...
            ],
            "error": str  # только если success=False
        }
    """
    if not config.TAVILY_API_KEY:
        return {"success": False, "error": "TAVILY_API_KEY не задан в .env"}

    try:
        logger.debug(f"[search] Запрос: {query!r} | deep={deep}")

        data = _tavily_request(
            query=query,
            max_results=max_results,
            search_depth="advanced" if deep else "basic",
        )

        results = [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "content": r.get("content", "")[:500],  # обрезаем длинные фрагменты
                "score":   round(r.get("score", 0), 3),
            }
            for r in data.get("results", [])
        ]

        logger.info(f"[search] Найдено {len(results)} результатов для: {query!r}")

        return {
            "success": True,
            "answer":  data.get("answer", ""),
            "results": results,
        }

    except httpx.TimeoutException:
        logger.warning(f"[search] Таймаут для запроса: {query!r}")
        return {"success": False, "error": "Таймаут — Tavily не ответил за 20 секунд"}

    except httpx.HTTPStatusError as e:
        logger.error(f"[search] HTTP {e.response.status_code} для: {query!r}")
        return {"success": False, "error": f"HTTP ошибка: {e.response.status_code}"}

    except Exception as e:
        logger.error(f"[search] Неожиданная ошибка: {e}")
        return {"success": False, "error": str(e)}


# ==============================
# ИНСТРУМЕНТЫ ДЛЯ АГЕНТА
# ==============================

@tool
def search_tool(query: str) -> str:
    """
    Ищет информацию в интернете по запросу.
    Используй когда нужно:
    - найти официальный сайт организации или администрации
    - найти общую информацию о чём-либо
    - найти контакты или адрес если других источников нет
    Возвращает краткий ответ и список найденных страниц с URL.
    """
    result = web_search(query, max_results=5)

    if not result["success"]:
        return f"Поиск не удался: {result['error']}"

    # Форматируем для агента
    lines = []

    if result["answer"]:
        lines.append(f"Краткий ответ: {result['answer']}\n")

    lines.append("Найденные страницы:")
    for i, r in enumerate(result["results"], 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['content']}")
        lines.append("")

    return "\n".join(lines)


@tool
def search_deep_tool(query: str) -> str:
    """
    Глубокий поиск в интернете — медленнее но точнее обычного поиска.
    Используй когда обычный поиск не дал нужного результата,
    или когда задача требует точных данных (официальные документы,
    реквизиты, контакты конкретной организации).
    """
    result = web_search(query, max_results=8, deep=True)

    if not result["success"]:
        return f"Глубокий поиск не удался: {result['error']}"

    lines = []

    if result["answer"]:
        lines.append(f"Краткий ответ: {result['answer']}\n")

    lines.append("Найденные страницы:")
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
    Исключает rusprofile.ru из поиска — ищет только официальные сайты.

    Параметры:
      adm_name:      полное название администрации из столбца E
      fields_needed: какие поля ищем (например "email телефон глава")
    """
    result = web_search(
        f"{adm_name} официальный сайт",
        max_results=5,
        deep=True
    )

    if not result["success"] or not result["results"]:
        return f"Официальный сайт не найден для: {adm_name}"

    # Фильтруем rusprofile и похожие агрегаторы
    skip_domains = ["rusprofile.ru", "egrul.ru", "checko.ru", "sbis.ru",
                    "list-org.com", "orgpage.ru", "kartoteka.ru"]

    official_urls = []
    for r in result["results"]:
        url = r["url"]
        if not any(d in url for d in skip_domains):
            official_urls.append(r)

    if not official_urls:
        return f"Официальный сайт не найден (только агрегаторы) для: {adm_name}"

    # Парсим первый официальный сайт
    from src.parser_new.tools.scraper_tool import scrape_smart
    import re

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