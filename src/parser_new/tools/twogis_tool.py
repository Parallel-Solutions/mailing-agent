"""
tools/twogis_tool.py — сбор организаций через 2ГИС + каскадное обогащение.

ЗАЧЕМ: Checko ищет по ОКВЭД внутри субъекта РФ — это плохо работает для мелкой
географии (район, небольшой город) и для сфер, которых нет в карте SECTOR_OKVED.
2ГИС ищет так, как ищет человек на карте: по тексту запроса внутри города, с
точными координатами и кураторскими рубриками.

ЧТО ОТДАЁТ 2ГИС (на ключе без платного contact_groups):
    название, адрес, координаты, рубрики.
ЧЕГО НЕ ОТДАЁТ:
    телефон, сайт, почту, ИНН.

ПОЭТОМУ КАСКАД:
    1. 2ГИС      — находит организации по географии и смыслу запроса
    2. Checko    — по названию (by=name) находит ИНН -> полные реквизиты + почта
    3. по сайту  — для тех, кого Checko не нашёл (ХУК enrich_by_site, см. ниже)

Точки входа:
    discover_via_2gis(...)        -> собрать + записать xlsx (аналог discover_and_write)
    discover_via_2gis_tool        -> @tool для LLM-агента

Формат xlsx тот же, что у discovery_tool (_write_batch_xlsx), поэтому файл
без изменений подхватывается импортёром получателей.
"""
from __future__ import annotations

import os
import re
import time

import httpx
from langchain.tools import tool

from src.infra.spend_ledger import record_service_call
from src.parser_new import config
from src.parser_new.logger import logger
from src.parser_new.tools.regions import get_region_code
from src.parser_new.tools.checko_tool import _checko_get
from src.parser_new.tools.discovery_tool import _enrich_one, _write_batch_xlsx

TWOGIS_BASE = "https://catalog.api.2gis.com"

# Радиус по умолчанию для поиска «вокруг точки» (мелкие города/районы), метры.
DEFAULT_RADIUS_M = 5000


# ==============================
# НИЗКОУРОВНЕВЫЙ ЗАПРОС К 2ГИС
# ==============================

def _twogis_key() -> str:
    """Ключ из config, иначе напрямую из окружения (config может его не объявлять)."""
    return (getattr(config, "TWOGIS_API_KEY", "") or os.getenv("TWOGIS_API_KEY", "")).strip()


def _twogis_get(path: str, params: dict) -> dict:
    """GET к 2ГИС. Бросает исключение при сетевой/HTTP ошибке."""
    key = _twogis_key()
    if not key:
        raise RuntimeError("TWOGIS_API_KEY не задан")
    resp = httpx.get(f"{TWOGIS_BASE}{path}", params={**params, "key": key}, timeout=20)
    resp.raise_for_status()
    record_service_call(service="twogis", operation="lookup", metadata={"path": path})
    return resp.json()


# ==============================
# ГЕОГРАФИЯ: РЕГИОН ИЛИ ТОЧКА
# ==============================

def resolve_geo(place: str) -> dict:
    """Определяет, как ограничить поиск по географии.

    В 2ГИС «регион» — это агломерация (крупный город со спутниками), она не
    совпадает с субъектом РФ. Поэтому:
      - «Санкт-Петербург» найдётся как регион    -> {"mode": "region", ...}
      - «Колпино» регионом НЕ найдётся           -> геокодируем в точку
                                                    -> {"mode": "point", ...}
    Возвращает {} если не удалось определить вообще.
    """
    text = (place or "").strip()
    if not text:
        return {}

    # 1) пробуем как регион 2ГИС
    try:
        data = _twogis_get("/2.0/region/search", {"q": text})
        items = ((data.get("result") or {}).get("items")) or []
        if items:
            rid = str(items[0].get("id") or "")
            logger.info(f"[2gis] Регион: {items[0].get('name')} (id={rid})")
            return {"mode": "region", "region_id": rid, "name": items[0].get("name", "")}
    except Exception as e:
        logger.warning(f"[2gis] region/search '{text}': {e}")

    # 2) не регион — геокодируем в точку (так ловятся районы и малые города)
    try:
        data = _twogis_get("/3.0/items/geocode",
                           {"q": text, "fields": "items.point,items.adm_div"})
        items = ((data.get("result") or {}).get("items")) or []
        for it in items:
            point = it.get("point") or {}
            lat, lon = point.get("lat"), point.get("lon")
            if lat and lon:
                subject = _subject_from_adm_div(it)
                logger.info(f"[2gis] Точка для '{text}': {lat},{lon} "
                            f"({it.get('full_name', '')}), субъект: {subject or '?'}")
                return {"mode": "point", "lat": float(lat), "lon": float(lon),
                        "name": it.get("full_name") or it.get("name") or text,
                        "subject": subject}
    except Exception as e:
        logger.warning(f"[2gis] geocode '{text}': {e}")

    return {}


def _subject_from_adm_div(item: dict) -> str:
    """Достаёт название субъекта РФ из административного деления 2ГИС.

    Так мы узнаём, что Колпино — это Санкт-Петербург, НЕ ведя свою карту городов:
    2ГИС сам знает иерархию. Нужно, чтобы затем спросить Checko по правильному
    региону. Приоритет: region -> city (для городов федерального значения
    субъект и город совпадают).
    """
    divs = item.get("adm_div") or []
    by_type = {d.get("type"): (d.get("name") or "") for d in divs if d.get("type")}
    for key in ("region", "city", "district_area", "settlement"):
        if by_type.get(key):
            return by_type[key]
    # запасной вариант — первый кусок полного имени ("Россия, Санкт-Петербург, ...")
    parts = [p.strip() for p in (item.get("full_name") or "").split(",") if p.strip()]
    for p in parts:
        if p.lower() not in {"россия", "russia"}:
            return p
    return ""


# ==============================
# ПОИСК ОРГАНИЗАЦИЙ В 2ГИС
# ==============================

def _region_id(name: str) -> str | None:
    """region_id по названию (для запасной стратегии «искать по всему субъекту»)."""
    if not name:
        return None
    try:
        data = _twogis_get("/2.0/region/search", {"q": name})
        items = ((data.get("result") or {}).get("items")) or []
        if items:
            return str(items[0].get("id") or "")
    except Exception as e:
        logger.warning(f"[2gis] region/search '{name}': {e}")
    return None


def search_places(query: str, geo: dict, limit: int, place: str = "",
                  radius_m: int = DEFAULT_RADIUS_M) -> list[dict]:
    """Карточки организаций из 2ГИС. Пробует несколько стратегий по очереди.

    Для точки (малый город/район) одна стратегия часто даёт пусто, поэтому идём
    каскадом: узкий радиус -> широкий радиус -> поиск по всему субъекту с
    названием места в тексте запроса и фильтром по адресу.
    """
    fields = "items.address,items.point,items.rubrics"
    PAGE_SIZE = 10          # жёсткий предел 2ГИС: допустимо от 1 до 10

    def _fetch(geo_params: dict, q: str) -> list[dict]:
        out: list[dict] = []
        max_pages = min(20, (limit // PAGE_SIZE) + 3)   # с запасом, но без транжирства
        for page in range(1, max_pages + 1):
            if len(out) >= limit:
                break
            try:
                data = _twogis_get("/3.0/items", {
                    **geo_params, "q": q, "fields": fields,
                    # один филиал на организацию: сети иначе занимают выдачу
                    # десятком карточек с одним и тем же ИНН
                    "search_type": "one_branch",
                    "page": page, "page_size": PAGE_SIZE,
                })
            except Exception as e:
                logger.error(f"[2gis] items {geo_params} q={q!r} стр {page}: {e}")
                break

            meta = data.get("meta") or {}
            items = ((data.get("result") or {}).get("items")) or []
            if not items:
                if page == 1:
                    # ВАЖНО: 2ГИС отдаёт HTTP 200 даже когда ничего не нашёл или
                    # ругается на параметры — причина лежит в meta, показываем её.
                    err = (meta.get("error") or {}) if isinstance(meta.get("error"), dict) else {}
                    logger.warning(
                        f"[2gis] пусто | meta.code={meta.get('code')} "
                        f"message={err.get('message') or meta.get('message') or '—'} "
                        f"| params={geo_params} q={q!r}"
                    )
                break
            out.extend(items)
            time.sleep(0.2)
        return out

    # --- стратегия 1: география прямо в тексте запроса ---
    # Документация 2ГИС разрешает «кафе в Тверском районе Москвы» — сервис сам
    # разбирает географию. Для района/малого города это работает лучше радиуса.
    if place:
        cards = _fetch({}, f"{query} в {place}")
        if cards:
            logger.info(f"[2gis] стратегия «текст: {query} в {place}» -> {len(cards)} карточек")
            return cards[:limit]

    # --- регион известен напрямую ---
    if geo.get("mode") == "region":
        cards = _fetch({"region_id": geo["region_id"]}, query)
        logger.info(f"[2gis] стратегия «регион {geo['region_id']}» -> {len(cards)} карточек")
        return cards[:limit]

    lat, lon = geo.get("lat"), geo.get("lon")
    point = f"{lon},{lat}"          # 2ГИС ждёт именно порядок lon,lat

    # --- стратегия 2: точка + узкий радиус ---
    cards = _fetch({"point": point, "radius": radius_m}, query)
    if cards:
        logger.info(f"[2gis] стратегия «точка+{radius_m}м» -> {len(cards)} карточек")
        return cards[:limit]

    # --- стратегия 3: тот же центр, радиус шире ---
    wide = min(radius_m * 4, 40000)
    cards = _fetch({"point": point, "radius": wide}, query)
    if cards:
        logger.info(f"[2gis] стратегия «точка+{wide}м» -> {len(cards)} карточек")
        return cards[:limit]

    # --- стратегия 4: весь субъект + фильтр по адресу ---
    rid = _region_id(geo.get("subject") or "")
    if rid:
        raw = _fetch({"region_id": rid}, f"{place} {query}".strip())
        needle = (place or "").strip().lower()[:6]      # корень названия места
        if needle:
            filtered = [
                c for c in raw
                if needle in ((c.get("full_name") or "") + " " +
                              (c.get("address_name") or "")).lower()
            ]
        else:
            filtered = raw
        logger.info(f"[2gis] стратегия «субъект {rid} + текст» -> {len(raw)} карточек, "
                    f"после фильтра по адресу «{needle}»: {len(filtered)}")
        return filtered[:limit]

    logger.warning("[2gis] все стратегии поиска дали пусто")
    return []


# ==============================
# СОПОСТАВЛЕНИЕ НАЗВАНИЙ 2ГИС <-> CHECKO
# ==============================

# В 2ГИС — бренд («ЕвроАвто, автосервис»), в Checko — юрлицо («ООО "ЕВРОАВТО"»).
# Чтобы сматчить, чистим и то и другое до «голого» названия.

_LEGAL_FORMS = ["ООО", "ОАО", "ЗАО", "ПАО", "НАО", "АНО", "НКО", "АО", "ИП",
                "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ",
                "АКЦИОНЕРНОЕ ОБЩЕСТВО"]


def _brand_name(twogis_name: str) -> str:
    """«ЕвроАвто, автосервис» -> «ЕвроАвто». Хвост после запятой — это рубрика."""
    return (twogis_name or "").split(",")[0].strip()


def _normalize_name(name: str) -> str:
    """Только буквы/цифры в верхнем регистре, без орг-формы и кавычек."""
    s = (name or "").upper()
    s = re.sub(r"[«»\"'`]", " ", s)
    for form in _LEGAL_FORMS:
        s = re.sub(rf"\b{form}\b", " ", s)
    return re.sub(r"[^А-ЯЁA-Z0-9]+", "", s)


def _names_match(a: str, b: str) -> bool:
    """Совпадение по вхождению нормализованных названий.
    Короткие названия (<4 символов) не матчим — слишком много ложных срабатываний."""
    na, nb = _normalize_name(a), _normalize_name(b)
    if len(na) < 4 or len(nb) < 4:
        return False
    return na in nb or nb in na


def _address_tokens(address: str) -> set[str]:
    """Значимые куски адреса: улицы и номера домов, без слов «улица/проспект/дом»."""
    stop = {"УЛИЦА", "УЛ", "ПРОСПЕКT", "ПРОСПЕКТ", "ПР", "ПРКТ", "ПЕРЕУЛОК", "ПЕР",
            "ШОССЕ", "Ш", "НАБЕРЕЖНАЯ", "НАБ", "БУЛЬВАР", "Б", "ДОМ", "Д", "КОРПУС",
            "К", "ЛИТ", "ЛИТЕРА", "СТРОЕНИЕ", "СТР", "ОФИС", "ПОМЕЩ", "КВ", "Г"}
    raw = re.split(r"[^А-ЯЁA-Z0-9]+", (address or "").upper())
    return {t for t in raw if t and t not in stop and len(t) > 1}


def _domain_roots(*texts: str) -> set[str]:
    """Корни значимых слов (4 буквы) — грубый, но рабочий отпечаток отрасли.
    «Легковой автосервис» -> {ЛЕГК, АВТО, СЕРВ...}"""
    roots: set[str] = set()
    for t in texts:
        for w in re.split(r"[^А-ЯЁA-Z]+", (t or "").upper()):
            if len(w) >= 5:
                roots.add(w[:4])
    return roots


def _okved_fits(rubric: str, query: str, okved_text: str) -> bool | None:
    """Похож ли ОКВЭД кандидата на то, что мы ищем?

    True  — профиль совпал (автосервис и есть автосервис)
    False — профиль явно чужой (нашли прививочный центр вместо автосервиса)
    None  — сравнивать не с чем, сигнала нет

    Это главный фильтр против однофамильцев: «Экспресс-сервис» в Питере носят
    десятки ООО, но автосервисов среди них единицы.
    """
    if not (okved_text or "").strip():
        return None
    want = _domain_roots(rubric, query)
    if not want:
        return None
    hay = okved_text.upper()
    return any(root in hay for root in want)


def _place_in_address(place: str, address: str) -> bool:
    """Упоминается ли искомый город/район в юридическом адресе."""
    needle = re.sub(r"[^А-ЯЁA-Z]", "", (place or "").upper())[:6]
    if len(needle) < 4:
        return False
    return needle in re.sub(r"[^А-ЯЁA-Z]", "", (address or "").upper())


def _score_candidate(brand: str, twogis_address: str, rubric: str,
                     query: str, place: str, rec: dict) -> tuple[int, bool]:
    """Оценка кандидата из Checko. Возвращает (балл, подтверждён_географией).

    Название — необходимое условие, но НЕ достаточное. Фильтр по ОКВЭД снимает
    однофамильцев из чужих отраслей, но бессилен против однофамильца в ТОЙ ЖЕ
    отрасли («Экспресс-сервис»-автосервис в Колпино и такой же в Полюстрово).
    Отличить их можно только адресом — поэтому он вынесен отдельным флагом.
    """
    cand_name = rec.get("НаимПолн") or rec.get("НаимСокр") or ""
    nb, na = _normalize_name(cand_name), _normalize_name(brand)
    if len(na) < 4 or len(nb) < 4:
        return 0, False

    if na == nb:
        score = 10
    elif na in nb or nb in na:
        score = 5
    else:
        return 0, False

    # --- профиль деятельности: снимает чужие отрасли ---
    fits = _okved_fits(rubric, query, rec.get("ОКВЭД", ""))
    if fits is False:
        return 0, False
    if fits is True:
        score += 8

    address = rec.get("ЮрАдрес", "")
    geo_confirmed = False

    # --- тот же город/район, что искали ---
    if _place_in_address(place, address):
        score += 6
        geo_confirmed = True

    # --- та же улица/дом, что показал 2ГИС ---
    common = _address_tokens(twogis_address) & _address_tokens(address)
    if common:
        score += 4 + min(len(common), 3)
        geo_confirmed = True

    return score, geo_confirmed


def _find_inn_by_name(brand: str, twogis_address: str, rubric: str,
                      query: str, place: str, region_code: str,
                      strict_geo: bool = True) -> tuple[str | None, str]:
    """Ищет ИНН в Checko по названию внутри региона.

    Возвращает (ИНН, причина_отказа). Причина пустая, если матч принят.
    strict_geo=True — принимать только матчи, подтверждённые адресом.
    """
    if len(_normalize_name(brand)) < 5:
        return None, ""          # «СТО», «Р3» — искать по ним бессмысленно

    try:
        resp = _checko_get("search", {
            "by": "name", "obj": "org", "query": brand,
            "region": region_code, "active": "true",
        })
    except Exception as e:
        logger.warning(f"[2gis->checko] search '{brand}': {e}")
        return None, ""

    if (resp.get("meta") or {}).get("status") == "error":
        return None, ""

    records = ((resp.get("data") or {}).get("Записи")) or []
    scored = []
    for rec in records:
        s, geo = _score_candidate(brand, twogis_address, rubric, query, place, rec)
        inn = (rec.get("ИНН") or "").strip()
        if s > 0 and inn:
            scored.append((s, geo, inn, rec.get("НаимПолн") or rec.get("НаимСокр") or ""))

    if not scored:
        return None, ""

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][0]
    top = [x for x in scored if x[0] == best]

    if len(top) > 1:
        logger.info(f"[2gis->checko] '{brand}': {len(top)} кандидатов с равным баллом "
                    f"{best} — ОТКЛОНЯЮ (выбор был бы наугад)")
        return None, "ambiguous"

    _, geo_confirmed, inn, cand_name = scored[0]

    if strict_geo and not geo_confirmed:
        logger.info(f"[2gis->checko] '{brand}' -> {cand_name}: название и отрасль сошлись, "
                    f"но адрес НЕ подтверждает — ОТКЛОНЯЮ (возможен однофамилец)")
        return None, "no_geo"

    if best <= 10:
        return None, "weak"

    return inn, ""


# ==============================
# ХУК: ДОБОР ПОЧТЫ ПО САЙТУ (ступень 3 каскада)
# ==============================

def enrich_by_site(company_name: str, address: str) -> dict:
    """Ступень 3: для тех, кого Checko не нашёл, — искать сайт и почту в интернете.

    ЗАГЛУШКА: сюда подключается логика из fill_emails.py (поиск по сниппетам
    Яндекса -> заход на топ-страницы -> парс mailto/подвала -> расшифровка JS).
    Пока возвращает пусто, поэтому такие организации просто не попадут в выгрузку
    (в неё берём только строки с почтой).

    Должна возвращать {"email": ..., "website": ..., "phone": ...} или {}.
    """
    return {}


# ==============================
# СБОР (чистые данные)
# ==============================

def collect_via_2gis(query: str, place: str, limit: int = 25,
                     radius_m: int = DEFAULT_RADIUS_M,
                     require_email: bool = True,
                     use_site_fallback: bool = False,
                     strict_geo: bool = True) -> dict:
    """Каскадный сбор: 2ГИС -> Checko по названию -> (опц.) добор по сайту.

    Возвращает {"success", "rows", "stats", "error"}.
    """
    if not _twogis_key():
        return {"success": False, "rows": [], "error": "TWOGIS_API_KEY не задан"}
    if not config.CHECKO_API_KEY:
        return {"success": False, "rows": [], "error": "CHECKO_API_KEY не задан"}

    # 1) География — спрашиваем у 2ГИС. Он же подскажет субъект РФ для Колпино
    #    и любого другого малого города, поэтому свою карту городов не ведём.
    geo = resolve_geo(place)
    if not geo:
        return {"success": False, "rows": [],
                "error": f"2ГИС не смог определить географию для '{place}'"}

    # 2) Код субъекта РФ — нужен Checko, чтобы искать по названию НЕ по всей стране.
    #    Порядок: подсказка 2ГИС -> имя региона 2ГИС -> исходный текст пользователя.
    region_code = None
    for candidate in (geo.get("subject"), geo.get("name"), place):
        if not candidate:
            continue
        region_code = get_region_code(candidate)
        if region_code:
            logger.info(f"[2gis] Субъект РФ для Checko: {candidate} -> код {region_code}")
            break
    if not region_code:
        return {"success": False, "rows": [],
                "error": f"Не удалось определить субъект РФ для '{place}' "
                         f"(нужен, чтобы искать в Checko по названию). "
                         f"2ГИС определил: {geo.get('subject') or geo.get('name') or '—'}"}

    # Матчинг теряет часть организаций — берём карточек с запасом.
    oversample = 4
    cards = search_places(query, geo, limit * oversample, place=place, radius_m=radius_m)
    if not cards:
        return {"success": False, "rows": [],
                "error": f"2ГИС не нашёл организаций по запросу '{query}' в '{place}'"}

    rows: list[dict] = []
    seen_inn: set[str] = set()
    stats = {"cards": len(cards), "matched": 0, "no_match": 0,
             "no_email": 0, "ambiguous": 0, "no_geo": 0}

    for card in cards:
        if len(rows) >= limit:
            break

        raw_name = card.get("name") or ""
        brand = _brand_name(raw_name)
        if not brand:
            continue

        twogis_address = card.get("address_name") or card.get("full_name") or ""
        rubrics = card.get("rubrics") or []
        primary_rubric = ""
        for r in rubrics:
            if r.get("kind") == "primary":
                primary_rubric = r.get("name", "")
                break
        if not primary_rubric and rubrics:
            primary_rubric = rubrics[0].get("name", "")

        # --- ступень 2: Checko по названию; рубрика и место — для проверки матча ---
        inn, reject_reason = _find_inn_by_name(brand, twogis_address, primary_rubric,
                                               query, place, region_code,
                                               strict_geo=strict_geo)
        if reject_reason == "ambiguous":
            stats["ambiguous"] += 1
        elif reject_reason == "no_geo":
            stats["no_geo"] += 1
        time.sleep(0.2)

        row: dict | None = None
        if inn and inn not in seen_inn:
            row = _enrich_one(inn)
            if row:
                row.pop("_status_raw", "")
                stats["matched"] += 1
                seen_inn.add(inn)
        elif inn in seen_inn:
            continue  # тот же юрлицо-дубль из другого филиала
        else:
            stats["no_match"] += 1

        # --- ступень 3: добор по сайту, если Checko не помог ---
        if row is None and use_site_fallback:
            extra = enrich_by_site(brand, twogis_address)
            if extra.get("email"):
                row = {
                    "company": brand, "contact_name": "",
                    "email": extra.get("email", ""), "email_fallback": "",
                    "inn": "", "kpp": "", "ogrn": "",
                    "phone": extra.get("phone", ""), "phone2": "",
                    "address": twogis_address, "industry": primary_rubric,
                    "status": "", "website": extra.get("website", ""),
                    "head_post": "", "source": "2ГИС+сайт",
                }

        if row is None:
            continue

        # 2ГИС точнее в адресе и отрасли — подставляем, если у Checko пусто
        if not (row.get("address") or "").strip():
            row["address"] = twogis_address
        if primary_rubric:
            row["industry"] = primary_rubric
        row["region"] = place
        row["source"] = row.get("source") or "2ГИС+Checko"
        if row["source"] == "Checko":
            row["source"] = "2ГИС+Checko"

        if require_email and not (row.get("email") or "").strip():
            stats["no_email"] += 1
            continue

        rows.append(row)

    logger.info(f"[2gis] Итог: карточек {stats['cards']}, сматчено {stats['matched']}, "
                f"отклонено (ничья) {stats['ambiguous']}, отклонено (адрес не подтвердил) "
                f"{stats['no_geo']}, без матча {stats['no_match']}, "
                f"без почты {stats['no_email']}, в выгрузке {len(rows)}")

    if not rows:
        return {"success": False, "rows": [], "stats": stats,
                "error": (f"В '{place}' по запросу '{query}' 2ГИС нашёл "
                          f"{stats['cards']} организаций, но ни одна не дала почту "
                          f"(не сматчилось с Checko: {stats['no_match']}, "
                          f"без почты: {stats['no_email']}).")}

    return {"success": True, "rows": rows, "stats": stats, "error": None}


# ==============================
# СБОР + ЗАПИСЬ XLSX
# ==============================

def discover_via_2gis(query: str, place: str, limit: int = 25,
                      radius_m: int = DEFAULT_RADIUS_M,
                      require_email: bool = True,
                      use_site_fallback: bool = False,
                      strict_geo: bool = True) -> dict:
    """Собирает через 2ГИС и сразу пишет batch_*.xlsx.
    Возвращает {"success", "path", "count", "stats", "error"}."""
    result = collect_via_2gis(query, place, limit, radius_m,
                              require_email, use_site_fallback, strict_geo)
    if not result["success"]:
        return {"success": False, "path": None, "count": 0,
                "stats": result.get("stats"), "error": result["error"]}

    path = _write_batch_xlsx(result["rows"])
    return {"success": True, "path": path, "count": len(result["rows"]),
            "stats": result.get("stats"), "error": None}


# ==============================
# ИНСТРУМЕНТ ДЛЯ АГЕНТА
# ==============================

@tool
def discover_via_2gis_tool(query: str, place: str, limit: int = 25) -> str:
    """
    Собирает список организаций через 2ГИС (карта) и обогащает реквизитами из Checko.

    Используй, когда нужны организации в КОНКРЕТНОМ ГОРОДЕ ИЛИ РАЙОНЕ
    ("автосервисы в Колпино", "кофейни на Васильевском острове") либо когда сфера
    не описывается кодом ОКВЭД ("магазины дверной фурнитуры").
    Для широких запросов по целому субъекту РФ лучше discover_companies_tool.

    Параметры:
      query: что ищем ("автосервисы", "кофейни", "дверная фурнитура")
      place: город или район ("Колпино", "Санкт-Петербург", "Зеленоград")
      limit: сколько организаций собрать (по умолчанию 25)
    """
    res = discover_via_2gis(query, place, limit)
    if not res["success"]:
        return f"Не удалось собрать: {res['error']}"
    st = res.get("stats") or {}
    return (f"Собрано организаций: {res['count']} "
            f"(карточек 2ГИС: {st.get('cards', '?')}, сматчено с Checko: {st.get('matched', '?')}). "
            f"Таблица готова для скачивания и импорта.")