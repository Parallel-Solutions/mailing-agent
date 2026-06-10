"""
email_lookup.py — поиск организации (ИНН) по её e-mail. Возврат (inn, liquidated, name).

ПРИВЯЗКА ИНН к почте (любой сигнал):
  - полная почта есть на странице; ИЛИ
  - на странице есть домен почты, и домен собственный (не провайдер); ИЛИ
  - страница найдена по названию организации и текст совпадает с названием (шаг 2).

ДВА ШАГА:
  1. Поиск по самой почте (пагинация, агрегаторы первыми). Если ИНН не нашёлся,
     но почта подтверждена на странице (например, на портале gosuslugi.ru) —
     с этой страницы вытаскиваем название АДМИНИСТРАЦИИ.
  2. Поиск по названию администрации → карточка агрегатора → ИНН (с проверкой
     совпадения названия; если ждём администрацию, на странице обязана быть «администрация»).
"""
from __future__ import annotations

import re
import time

import httpx
from bs4 import BeautifulSoup

try:
    from src.parser_new import config
    from src.parser_new.logger import logger
    from src.parser_new.batch_processor import (
        _get_yandex_search, _parse_yandex_xml, _extract_inn_from_text, HEADERS,
    )
except ImportError:
    import config
    from logger import logger
    from batch_processor import (
        _get_yandex_search, _parse_yandex_xml, _extract_inn_from_text, HEADERS,
    )


PUBLIC_PROVIDERS = {
    "yandex.ru", "ya.ru", "yandex.com", "mail.ru", "list.ru", "bk.ru", "inbox.ru",
    "internet.ru", "gmail.com", "googlemail.com", "rambler.ru", "myrambler.ru",
    "ro.ru", "outlook.com", "hotmail.com", "live.com", "icloud.com", "yahoo.com",
    "mail.com",
}

_AGGREGATORS = ("rusprofile.ru", "list-org.com", "companium.ru", "audit-it.ru",
                "sbis.ru", "zachestnyibiznes.ru", "kartoteka.ru", "egrul",
                "sravni.ru", "bo.nalog")

_SKIP_DOMAINS = ("yandex.", "google.", "vk.com", "ok.ru", "2gis.", "wikipedia.",
                 "web.archive.org", "mail.ru", "t.me", "facebook.")

_PAGES = 3
_MAX_PAGES = 8
_EMPTY = (None, False, "")


def _email_domain(email: str) -> str:
    return email.split("@", 1)[1].lower().strip() if "@" in email else ""


def _url_domain(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url)
    host = (m.group(1) if m else url).lower()
    return host[4:] if host.startswith("www.") else host


def _yandex_query(search, query: str, page: int = 0) -> list[dict]:
    time.sleep(0.5)
    xml = search.run(query, format="xml", page=page)
    return _parse_yandex_xml(xml)


def _yandex_multi(search, query: str, pages: int = _PAGES) -> list[dict]:
    out, seen = [], set()
    for pg in range(pages):
        try:
            for r in _yandex_query(search, query, page=pg):
                u = r.get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    out.append(r)
        except Exception as e:
            logger.debug(f"[email->inn] страница {pg} запроса {query!r}: {e}")
            break
    return out


def _fetch_html(url: str, attempts: int = 2) -> str | None:
    last = ""
    for i in range(attempts):
        try:
            time.sleep(1.5 if i == 0 else 4.0)
            resp = httpx.get(url, headers=HEADERS, timeout=12, follow_redirects=True)
            if resp.status_code == 200 and len(resp.text) > 500:
                return resp.text
            last = f"HTTP {resp.status_code}, len={len(resp.text)}"
        except Exception as e:
            last = str(e)
        logger.debug(f"[email->inn] попытка {i + 1} для {url[:50]}: {last}")
    return None


def _significant_words(s: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9-]{5,}", s.lower())


def _name_in_text(name: str, text_low: str) -> bool:
    words = _significant_words(name)
    if not words:
        return False
    hits = sum(1 for w in words if w in text_low)
    return hits >= max(2, int(len(words) * 0.6))


def _org_name_from_page(soup: BeautifulSoup, text: str) -> str:
    """Вытаскивает название АДМИНИСТРАЦИИ со страницы (gosuslugi и т.п.)."""
    m = re.search(
        r"Администраци\w*\s+[^.,;:\n«»\"()]{4,90}?(?:округа|района|поселения|сельсовета|города)\b",
        text, re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(0)).strip()
    m = re.search(r"Муниципальн\w+ образовани\w+\s+[^.,;:\n«»\"()]{4,90}", text, re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(0)).strip()
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    return ""


def _rusprofile_meta(soup: BeautifulSoup) -> tuple[str, bool]:
    nm = soup.select_one("[itemprop='legalName'] .copy_target")
    name = nm.get_text(strip=True) if nm else ""
    liquidated = False
    st = soup.select_one(".warning-text")
    if st:
        s = st.get_text().lower()
        if "ликвид" in s or "не действ" in s:
            liquidated = True
    return name, liquidated


def _scan(results: list[dict], email: str, name_hint: str | None) -> tuple[tuple | None, str]:
    """Пробует кандидатов (агрегаторы первыми, 1 страница на домен).
    Возвращает (найденная_тройка|None, найденное_название_для_шага_2)."""
    agg, other, seen_dom = [], [], set()
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        dom = _url_domain(url)
        if dom in seen_dom or any(s in dom for s in _SKIP_DOMAINS):
            continue
        seen_dom.add(dom)
        (agg if any(a in url for a in _AGGREGATORS) else other).append(url)

    domain = _email_domain(email)
    want_adm = bool(name_hint) and "администрац" in (name_hint or "").lower()
    best_name = ""

    for url in (agg + other)[:_MAX_PAGES]:
        html = _fetch_html(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        low = text.lower()

        email_present = email.lower() in low
        domain_ok = bool(domain) and domain not in PUBLIC_PROVIDERS and domain in low
        name_ok = bool(name_hint) and _name_in_text(name_hint, low)
        if want_adm and "администрац" not in low:
            name_ok = False

        if email_present or domain_ok or name_ok:
            inn = _extract_inn_from_text(text)
            if inn:
                name, liquidated = _rusprofile_meta(soup) if "rusprofile.ru" in url else ("", False)
                mark = " (ликвидирована)" if liquidated else ""
                logger.info(f"[email->inn] {url[:55]}: ИНН {inn}{mark}")
                return (inn, liquidated, name), best_name
            # ИНН нет, но почта/домен подтвердились → запомним название для шага 2
            if name_hint is None and (email_present or domain_ok):
                nm = _org_name_from_page(soup, text)
                better = nm and (not best_name
                                 or ("администрац" in nm.lower() and "администрац" not in best_name.lower()))
                if better:
                    best_name = nm
    return None, best_name


def search_inn_by_email(email: str) -> tuple[str | None, bool, str]:
    if not email or "@" not in email:
        return _EMPTY
    if not config.YANDEX_API_KEY or not config.YANDEX_FOLDER_ID:
        logger.debug("[email->inn] нет ключей Яндекса — пропуск")
        return _EMPTY

    email = email.strip()
    try:
        search = _get_yandex_search()
        results = _yandex_multi(search, email)
    except Exception as e:
        logger.warning(f"[email->inn] Яндекс недоступен: {e}")
        return _EMPTY

    # ШАГ 1: по почте
    found, org_name = _scan(results, email, name_hint=None)
    if found:
        return found

    # ШАГ 2: по названию администрации, добытому со страницы с почтой
    if org_name:
        territory = re.sub(r"(?i)^муниципальн\w+ образовани\w+\s+", "", org_name).strip()
        if "администрац" in org_name.lower():
            q = org_name
        else:
            q = "администрация " + (territory or org_name)
        logger.info(f"[email->inn] по почте ИНН нет, ищу по организации: {q[:70]}")
        try:
            results2 = _yandex_multi(search, q)
        except Exception as e:
            logger.warning(f"[email->inn] поиск по названию упал: {e}")
            results2 = []
        found, _ = _scan(results2, email, name_hint=q)
        if found:
            return found

    logger.info(f"[email->inn] по почте {email} подтверждённый ИНН не найден")
    return _EMPTY