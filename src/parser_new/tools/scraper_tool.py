"""
tools/scraper_tool.py — парсер веб-страниц.

Два режима:
  - Быстрый (httpx + BeautifulSoup) — для обычных HTML сайтов
  - Браузерный (Playwright) — для сайтов с JavaScript (React, Vue и т.д.)

Агент сам выбирает режим. Если быстрый вернул пустую страницу — автоматически
переключается на браузерный.
"""
from __future__ import annotations

import re
import httpx
from bs4 import BeautifulSoup
from langchain.tools import tool
from tenacity import retry, stop_after_attempt, wait_fixed

from src.infra.spend_ledger import record_service_call
from src.parser_new.logger import logger
from src.parser_new import config


# ==============================
# ЗАГОЛОВКИ — маскируемся под браузер
# ==============================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ==============================
# УТИЛИТЫ
# ==============================

def _clean_text(text: str) -> str:
    """Убирает лишние пробелы и пустые строки."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _is_empty_page(text: str) -> bool:
    """Проверяет что страница не пустая и не заглушка."""
    return len(text.strip()) < 200


def _extract_contacts(soup: BeautifulSoup) -> dict:
    """
    Вытаскивает контактные данные из любой страницы.
    Ищет телефоны, email, адреса по паттернам.
    """
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
        "phones": list(set(phones))[:5],   # убираем дубли, берём до 5
        "emails": list(set(emails))[:5],
    }


def _parse_html(html: str, url: str) -> dict:
    """
    Парсит HTML и извлекает структурированные данные:
    заголовок, основной текст, контакты, все ссылки.
    """
    soup = BeautifulSoup(html, "lxml")

    # Убираем мусор — скрипты, стили, попапы
    for tag in soup(["script", "style", "noscript", "iframe", "nav", "footer"]):
        tag.decompose()

    title   = soup.title.string.strip() if soup.title else ""
    text    = _clean_text(soup.get_text())
    contacts = _extract_contacts(soup)

    # Собираем все ссылки — агент может пойти по ним дальше
    links = []
    for a in soup.find_all("a", href=True):
        href  = a["href"]
        label = a.get_text(strip=True)
        # Берём только осмысленные внутренние ссылки
        if href.startswith("/") or url.split("/")[2] in href:
            if label and len(label) < 100:
                links.append({"label": label, "href": href})

    return {
        "title":    title,
        "text":     text[:3000],  # обрезаем чтобы не перегружать контекст агента
        "contacts": contacts,
        "links":    links[:20],   # до 20 ссылок
    }


# ==============================
# БЫСТРЫЙ ПАРСЕР (httpx + BS4)
# ==============================

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_static(url: str) -> tuple[str, int]:
    """Скачивает страницу через httpx. Возвращает (html, status_code)."""
    response = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
    return response.text, response.status_code


def scrape_static(url: str) -> dict:
    """
    Быстрый парсинг статичного HTML сайта.

    Returns:
        {"success": bool, "data": {...}, "error": str}
    """
    try:
        logger.debug(f"[scraper/static] {url}")
        html, status = _fetch_static(url)

        if status >= 400:
            return {"success": False, "error": f"Сервер вернул HTTP {status}"}

        data = _parse_html(html, url)

        if _is_empty_page(data["text"]):
            return {"success": False, "error": "Страница пустая — возможно нужен браузерный парсер"}

        logger.info(f"[scraper/static] Успешно: {url} ({len(data['text'])} симв.)")
        return {"success": True, "data": data}

    except httpx.TimeoutException:
        return {"success": False, "error": "Таймаут — сайт не ответил за 15 секунд"}
    except httpx.ConnectError:
        return {"success": False, "error": "Не удалось подключиться к сайту"}
    except Exception as e:
        logger.error(f"[scraper/static] Ошибка: {e}")
        return {"success": False, "error": str(e)}


# ==============================
# БРАУЗЕРНЫЙ ПАРСЕР (Playwright)
# ==============================

def scrape_browser(url: str) -> dict:
    """
    Парсинг через настоящий браузер — для JS сайтов.
    Медленнее (3-10 сек), но видит всё что видит человек в браузере.
    """
    try:
        from playwright.sync_api import sync_playwright

        logger.debug(f"[scraper/browser] {url}")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)  # headless = без окна
            page = browser.new_page(extra_http_headers=HEADERS)

            # Блокируем картинки и шрифты — ускоряет загрузку
            page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2}", lambda r: r.abort())

            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # ждём JS рендеринг

            html = page.content()
            browser.close()

        data = _parse_html(html, url)

        if _is_empty_page(data["text"]):
            return {"success": False, "error": "Страница пустая даже в браузере"}

        logger.info(f"[scraper/browser] Успешно: {url} ({len(data['text'])} симв.)")
        return {"success": True, "data": data}

    except Exception as e:
        logger.error(f"[scraper/browser] Ошибка: {e}")
        return {"success": False, "error": str(e)}


# ==============================
# УМНЫЙ ПАРСЕР — пробует оба режима
# ==============================

def scrape_smart(url: str) -> dict:
    """
    Сначала пробует быстрый парсинг.
    Если страница пустая — автоматически переключается на браузерный.
    """
    result = scrape_static(url)

    if not result["success"] and "пустая" in result.get("error", ""):
        logger.info(f"[scraper] Статичный парсер не справился — пробую браузер: {url}")
        result = scrape_browser(url)

    return result


# ==============================
# ФОРМАТИРОВАНИЕ ДЛЯ АГЕНТА
# ==============================

def _format_result(result: dict, url: str) -> str:
    """Переводит результат парсинга в текст для агента."""
    if not result["success"]:
        return f"Не удалось получить страницу {url}: {result['error']}"

    d = result["data"]
    lines = [
        f"Страница: {url}",
        f"Заголовок: {d['title']}",
        "",
        "--- Текст страницы ---",
        d["text"],
    ]

    contacts = d.get("contacts", {})
    if contacts.get("phones"):
        lines.append(f"\nТелефоны: {', '.join(contacts['phones'])}")
    if contacts.get("emails"):
        lines.append(f"Email: {', '.join(contacts['emails'])}")

    if d.get("links"):
        lines.append("\nСсылки на странице:")
        for link in d["links"][:10]:
            lines.append(f"  {link['label']} → {link['href']}")

    return "\n".join(lines)


# ==============================
# ИНСТРУМЕНТЫ ДЛЯ АГЕНТА
# ==============================

@tool
def scraper_tool(url: str) -> str:
    """
    Открывает веб-страницу и извлекает из неё текст, контакты и ссылки.
    Используй когда у тебя есть конкретный URL и нужно прочитать содержимое страницы.
    Автоматически выбирает между быстрым и браузерным режимом.
    Возвращает текст страницы, найденные телефоны, email и список ссылок.
    """
    logger.info(f"[scraper_tool] Парсю: {url}")
    result = scrape_smart(url)
    return _format_result(result, url)


@tool
def scraper_contacts_tool(url: str) -> str:
    """
    Парсит страницу и возвращает ТОЛЬКО контактные данные:
    телефоны и email адреса.
    Используй когда нужно быстро достать контакты с конкретной страницы
    и не нужен весь текст.
    """
    logger.info(f"[scraper_contacts_tool] Ищу контакты: {url}")
    result = scrape_smart(url)

    if not result["success"]:
        return f"Не удалось получить страницу: {result['error']}"

    contacts = result["data"].get("contacts", {})
    phones = contacts.get("phones", [])
    emails = contacts.get("emails", [])

    if not phones and not emails:
        return f"На странице {url} контактов не найдено."

    lines = [f"Контакты на странице {url}:"]
    if phones:
        lines.append(f"Телефоны: {', '.join(phones)}")
    if emails:
        lines.append(f"Email: {', '.join(emails)}")

    return "\n".join(lines)


@tool
def scraper_links_tool(url: str) -> str:
    """
    Парсит страницу и возвращает ТОЛЬКО список ссылок на ней.
    Используй когда нужно найти нужный раздел сайта —
    например страницу с контактами или руководством администрации.
    """
    logger.info(f"[scraper_links_tool] Собираю ссылки: {url}")
    result = scrape_static(url)  # для ссылок браузер обычно не нужен

    if not result["success"]:
        return f"Не удалось получить страницу: {result['error']}"

    links = result["data"].get("links", [])
    if not links:
        return f"На странице {url} ссылок не найдено."

    lines = [f"Ссылки на странице {url}:"]
    for link in links:
        lines.append(f"  {link['label']} → {link['href']}")

    return "\n".join(lines)








@tool
def rusprofile_tool(mun_name: str, district: str, region: str) -> str:
    """
    Проверяет статус администрации МО на rusprofile.ru через Яндекс Search API.
    Статус определяется из сниппета — без захода на страницу.
    ИНН извлекается из сниппета или со страницы.

    Параметры:
      mun_name: название МО
      district: муниципальный район
      region:   субъект РФ
    """
    from bs4 import BeautifulSoup
    import httpx
    import re as _re
    import time

    def check_org_valid(name: str) -> bool:
        name_lower = name.lower()
        bad = ["культур", "досуг", "библиотек", "музей", "школ", "больниц",
               "спорт", "казначейств", "налогов", "пенсион", "соцзащит", "мфц",
               "водоканал", "жкх", "электросет", "колхоз", "совхоз"]
        if any(k in name_lower for k in bad):
            return False
        good = ["администрац", "сельсовет", "поселени", "мэри", "управ", "округ"]
        return any(k in name_lower for k in good)

    def check_liquidated(text: str) -> bool:
        t = text.lower()
        return bool(
            _re.search(r"статус[:\s]+ликвидирован", t) or
            _re.search(r"организация ликвидирована", t) or
            _re.search(r"ликвидирована с \d{2}\.\d{2}\.\d{4}", t)
        )

    def parse_yandex_xml(xml_bytes: bytes) -> list[dict]:
        try:
            xml_text = xml_bytes.decode("utf-8")
        except Exception:
            return []
        results = []
        for doc in _re.findall(r"<doc>(.*?)</doc>", xml_text, _re.DOTALL):
            url_m = _re.search(r"<url>(.*?)</url>", doc)
            if not url_m:
                continue
            title_m = _re.search(r"<title>(.*?)</title>", doc, _re.DOTALL)
            snippet_m = _re.search(r"<passages>(.*?)</passages>", doc, _re.DOTALL)
            url = url_m.group(1).strip()
            title = _re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
            snippet = _re.sub(r"<[^>]+>", "", snippet_m.group(1)).strip() if snippet_m else ""
            results.append({"url": url, "title": title, "snippet": snippet})
        return results

    if not config.YANDEX_API_KEY or not config.YANDEX_FOLDER_ID:
        return f"RUSPROFILE_NOT_FOUND: YANDEX_API_KEY или YANDEX_FOLDER_ID не заданы в .env"

    try:
        from yandex_ai_studio_sdk import AIStudio
        sdk = AIStudio(folder_id=config.YANDEX_FOLDER_ID, auth=config.YANDEX_API_KEY)
        search = sdk.search_api.web("RU", groups_on_page=5)

        query = f"site:rusprofile.ru {region} {district} {mun_name} администрация"
        logger.info(f"[rusprofile] Яндекс поиск: {query}")

        time.sleep(0.5)
        xml_result = search.run(query, format="xml", page=0)
        record_service_call(service="yandex", operation="search")
        parsed = parse_yandex_xml(xml_result)

        # Берём первый подходящий результат с /id/ в URL
        best = None
        for r in parsed:
            if "rusprofile.ru" in r["url"] and "/id/" in r["url"]:
                best = r
                break

        if not best:
            return f"RUSPROFILE_NOT_FOUND: {mun_name}"

        company_url = best["url"]
        title = best.get("title", "")
        snippet = best.get("snippet", "")
        logger.info(f"[rusprofile] Лучший результат: {title}")

        # Проверяем что это орган власти
        if title and not check_org_valid(title):
            logger.warning(f"[rusprofile] Нерелевантная организация: {title}")
            return f"RUSPROFILE_NOT_FOUND: нерелевантная организация ({title})"

        full_text = title + " " + snippet

        # Определяем статус из сниппета
        if check_liquidated(full_text):
            logger.info(f"[rusprofile] Ликвидирована (из сниппета): {title}")
            return (
                f"ЛИКВИДИРОВАНА: {title}\n"
                f"ДЕЙСТВИЕ: записать ADM_NAME=ликвидирована, перейти к следующему МО"
            )

        # Извлекаем ИНН из сниппета
        inn = ""
        inn_match = _re.search(r"ИНН[/\s:]+(\d{10})", full_text)
        if inn_match:
            inn = inn_match.group(1)

        # Если ИНН не в сниппете — заходим на страницу
        if not inn:
            try:
                time.sleep(2)
                resp = httpx.get(company_url, headers=HEADERS, timeout=10, follow_redirects=True)
                soup = BeautifulSoup(resp.text, "lxml")

                # Проверяем статус на странице
                status_el = soup.select_one(".warning-text")
                if status_el and ("ликвид" in status_el.get_text().lower() or
                                  "не действ" in status_el.get_text().lower()):
                    logger.info(f"[rusprofile] Ликвидирована (со страницы): {title}")
                    return (
                        f"ЛИКВИДИРОВАНА: {title}\n"
                        f"ДЕЙСТВИЕ: записать ADM_NAME=ликвидирована, перейти к следующему МО"
                    )

                for row in soup.select(".company-row"):
                    txt = row.get_text()
                    if "ИНН" in txt:
                        m = _re.search(r"\b\d{10}\b", txt)
                        if m:
                            inn = m.group()
                            break
            except Exception as e:
                logger.debug(f"[rusprofile] Ошибка при заходе на страницу: {e}")

        logger.info(f"[rusprofile] Действует: {title}, ИНН: {inn}")
        if inn:
            return (
                f"ДЕЙСТВУЕТ: {title}\n"
                f"ИНН: {inn}\n"
                f"ДЕЙСТВИЕ: вызови checko_company_tool('{inn}')"
            )
        return f"ДЕЙСТВУЕТ: {title}\nИНН не найден — используй checko_search_tool"

    except Exception as e:
        logger.error(f"[rusprofile] Ошибка: {e}")
        return f"RUSPROFILE_NOT_FOUND: {mun_name} — {e}"