"""
tools/collector.py — единая точка сбора получателей.

Пользователь пишет «найди 10 автосервисов в Колпино» — здесь разбирается запрос
и по очереди опрашиваются источники, пока не наберётся нужное количество.

ПОРЯДОК ИСТОЧНИКОВ (от точного к широкому):

  1. 2ГИС + Checko  — точная география (город, район, окрестности точки).
                      Даёт мало строк, но они проверены адресом. Единственный
                      источник, который умеет работать УЖЕ районом города.

  2. Checko по ОКВЭД — широкий обход всего субъекта РФ. Даёт много, но
                      география грубая: только уровень субъекта.

ВАЖНОЕ ПРАВИЛО ГЕОГРАФИИ:
    источник 2 включается ТОЛЬКО если пользователь назвал субъект РФ
    («Москва», «Московская область»). Для района или малого города («Колпино»)
    добор по субъекту дал бы автосервисы со всего Питера — формально строки
    появятся, а фактически это будет обман. Поэтому в таком случае честно
    возвращаем сколько нашли и говорим почему.

Точка входа: collect_recipients(query, place, limit)
"""
from __future__ import annotations

import re

from src.parser_new.logger import logger
from src.parser_new.tools.regions import get_region_code
from src.parser_new.tools.discovery_tool import (
    discover_companies,
    resolve_okved,
    _write_batch_xlsx,
)
from src.parser_new.tools.twogis_tool import collect_via_2gis


# ==============================
# РАЗБОР ЗАПРОСА ПОЛЬЗОВАТЕЛЯ
# ==============================

def parse_request(text: str) -> tuple[str, str, int] | None:
    """Разбирает запрос на сбор -> (что, где, сколько). None — если это не он.

    Понимает два вида фраз:

    1) Промпт из модалки RecipientGenerateModal (основной путь из UI):
         «Найди Строительные магазины в регионе: Москва. Нужны данные: email,
          телефон, адрес. Объём: 10. После сбора подготовь таблицу...»
       Здесь важно: место — только до первой точки, а объём берётся из «Объём: N»,
       а НЕ первым числом во фразе.

    2) Живую фразу человеком: «найди 10 автосервисов в Колпино».
    """
    s = (text or "").strip()
    if not s:
        return None

    # --- объём: сначала явное «Объём: N», иначе первое число ---
    m_vol = re.search(r"объ[её]м\s*[:\-]?\s*(\d{1,4})", s, re.IGNORECASE)
    if m_vol:
        limit = int(m_vol.group(1))
    else:
        m_num = re.search(r"\b(\d{1,4})\b", s)
        limit = int(m_num.group(1)) if m_num else 25
    limit = max(1, min(limit, 500))

    # --- отрезаем служебные хвосты модалки ---
    body = s
    for marker in (r"нужны\s+данные", r"объ[её]м\s*[:\-]", r"после\s+сбора",
                   r"какие\s+данные"):
        body = re.split(marker, body, maxsplit=1, flags=re.IGNORECASE)[0]

    # --- убираем вводные глаголы ---
    body = re.sub(r"^\s*(найди|найти|собери|собрать|подбери|нужн[ыо]|дай)\w*\s*",
                  "", body, flags=re.IGNORECASE).strip()
    # число из живой фразы («10 автосервисов»), в промпте модалки его тут уже нет
    body = re.sub(r"\b\d{1,4}\b", " ", body, count=1).strip()

    # --- «в регионе: X» / «в регионе X» -> «в X» ---
    body = re.sub(r"\bв\s+регионе\b\s*[:\-]?\s*", "в ", body, flags=re.IGNORECASE)
    body = re.sub(r"\bрегион\s*[:\-]\s*", "", body, flags=re.IGNORECASE)

    # --- география: по последнему « в » ---
    parts = re.split(r"\s+в[о]?\s+", body, flags=re.IGNORECASE)
    if len(parts) < 2:
        return None

    place = parts[-1]
    query = " в ".join(parts[:-1])

    # место — только до первой точки: дальше начинается следующее предложение
    place = re.split(r"[.;\n]", place)[0]
    place = place.strip(" .,;:!?»«\"'")
    query = re.split(r"[.;\n]", query)[0].strip(" .,;:!?»«\"'")

    if not place or not query:
        return None

    return query, place, limit


# ==============================
# ОРГАНЫ ВЛАСТИ — НЕ СЮДА
# ==============================

# Администрации, министерства и прочие госорганы собираются другим маршрутом:
# список МО берётся из классификатора ОКТМО, ведомства — поиском по названию в
# Checko. Коллектор ищет по ОКВЭД и карте 2ГИС, а у органов власти коммерческого
# ОКВЭД нет, поэтому он вернул бы пусто или случайные организации.
_GOV_MARKERS = (
    "администрац", "муниципальн", "поселени", "сельсовет", "сельское поселение",
    "мэри", "управа", "органы власти", "орган власти", "местного самоуправлени",
    "министерств", "департамент", "комитет", "госорган",
    "правительств", "губернатор", "дума", "законодательн", "префектур",
    "росреестр", "роспотребнадзор", "ростехнадзор", "прокуратур",
)


def _detect_government(text: str) -> str:
    """Возвращает найденный маркер госоргана или пустую строку."""
    low = (text or "").lower()
    for marker in _GOV_MARKERS:
        if marker in low:
            return marker
    return ""


# ==============================
# СЛИЯНИЕ РЕЗУЛЬТАТОВ БЕЗ ДУБЛЕЙ
# ==============================

def _norm_name(name: str) -> str:
    """Название без орг-формы, кавычек и знаков — для сравнения дублей."""
    s = (name or "").upper()
    s = re.sub(r"[«»\"'`]", " ", s)
    for form in ("ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ", "АКЦИОНЕРНОЕ ОБЩЕСТВО",
                 "ООО", "ОАО", "ЗАО", "ПАО", "НАО", "АНО", "АО", "ИП"):
        s = re.sub(rf"\b{form}\b", " ", s)
    return re.sub(r"[^А-ЯЁA-Z0-9]+", "", s)


def _row_key(row: dict) -> str:
    """Ключ дедупликации: ИНН, если есть, иначе название + почта."""
    inn = (row.get("inn") or "").strip()
    if inn:
        return f"inn:{inn}"
    name = _norm_name(row.get("company", ""))
    email = (row.get("email") or "").strip().lower()
    return f"nm:{name}|{email}"


def _merge(dst: list[dict], src: list[dict], seen: set[str], limit: int) -> int:
    """Добавляет строки из src в dst, пропуская дубли. Возвращает сколько добавил."""
    added = 0
    for row in src:
        if len(dst) >= limit:
            break
        if not (row.get("email") or "").strip():
            continue
        key = _row_key(row)
        if key in seen:
            continue
        seen.add(key)
        dst.append(row)
        added += 1
    return added


# ==============================
# ОСНОВНОЙ СБОР
# ==============================

def _subject_code(place: str) -> str | None:
    """Код субъекта РФ, если пользователь назвал именно субъект. Иначе None.

    Пользователь пишет в падеже («в Москве»), а справочник регионов ждёт
    именительный. Поэтому если прямое сопоставление не вышло — спрашиваем 2ГИС:
    он понимает падежи и возвращает название в нормальной форме.

    Для района или города внутри субъекта («Колпино») возвращает None —
    это и есть сигнал «добирать по субъекту нельзя».
    """
    code = get_region_code(place)
    if code:
        return code
    try:
        from src.parser_new.tools.twogis_tool import resolve_geo
        geo = resolve_geo(place)
        # только mode=region: для точки subject — это РОДИТЕЛЬСКИЙ субъект,
        # а сам запрошенный объект субъектом не является
        if geo.get("mode") == "region":
            return get_region_code(geo.get("name") or "")
    except Exception as e:
        logger.warning(f"[collector] нормализация места '{place}': {e}")
    return None


def collect_recipients(query: str, place: str, limit: int = 25) -> dict:
    """Опрашивает источники по очереди, пока не наберётся limit строк с почтой.

    Возвращает {"success", "path", "count", "requested", "sources", "note", "error"}.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    sources: list[dict] = []
    notes: list[str] = []

    subject_code = _subject_code(place)
    is_subject = subject_code is not None
    logger.info(f"[collector] запрос={query!r} место={place!r} нужно={limit} "
                f"(место — {'субъект РФ' if is_subject else 'район/город внутри субъекта'})")

    # ---------- Источник 1: 2ГИС + Checko ----------
    try:
        r1 = collect_via_2gis(query, place, limit=limit,
                              require_email=True, strict_geo=True)
        if r1.get("success"):
            added = _merge(rows, r1.get("rows") or [], seen, limit)
            sources.append({"name": "2ГИС+Checko", "added": added,
                            "stats": r1.get("stats")})
            logger.info(f"[collector] 2ГИС дал {added} строк, всего {len(rows)}/{limit}")
        else:
            sources.append({"name": "2ГИС+Checko", "added": 0, "error": r1.get("error")})
            logger.info(f"[collector] 2ГИС: {r1.get('error')}")
    except Exception as e:
        logger.warning(f"[collector] источник 2ГИС упал: {e}")
        sources.append({"name": "2ГИС+Checko", "added": 0, "error": str(e)})

    # ---------- Источник 2: Checko по ОКВЭД (только по субъекту РФ) ----------
    if len(rows) < limit:
        if not is_subject:
            notes.append(
                f"«{place}» — это район или город внутри субъекта. Добирать "
                f"через реестр по всему субъекту не стал: пришли бы организации "
                f"из других районов. Показываю только то, что действительно там."
            )
        elif resolve_okved(query) is None:
            notes.append(
                f"Отрасль по запросу «{query}» не описана кодом ОКВЭД, поэтому "
                f"добор по реестру недоступен. Можно добавить код в SECTOR_OKVED."
            )
        else:
            need = limit - len(rows)
            try:
                # берём с запасом: часть отсеется дедупом и фильтром по почте
                r2 = discover_companies(query, place, limit=need * 3)
                if r2.get("success"):
                    added = _merge(rows, r2.get("rows") or [], seen, limit)
                    sources.append({"name": "Checko/ОКВЭД", "added": added})
                    logger.info(f"[collector] Checko дал {added} строк, всего {len(rows)}/{limit}")
                else:
                    sources.append({"name": "Checko/ОКВЭД", "added": 0,
                                    "error": r2.get("error")})
            except Exception as e:
                logger.warning(f"[collector] источник Checko упал: {e}")
                sources.append({"name": "Checko/ОКВЭД", "added": 0, "error": str(e)})

    # ---------- Итог ----------
    if not rows:
        detail = "; ".join(
            f"{s['name']}: {s.get('error') or 'ничего не найдено'}" for s in sources
        )
        return {"success": False, "path": None, "count": 0, "requested": limit,
                "sources": sources, "note": " ".join(notes),
                "error": f"Не удалось собрать ни одной организации с почтой. {detail}"}

    path = _write_batch_xlsx(rows)

    if len(rows) < limit:
        notes.append(f"Запрошено {limit}, найдено {len(rows)} — больше "
                     f"организаций с почтой по этим параметрам не нашлось.")

    logger.info(f"[collector] ИТОГО {len(rows)}/{limit} строк -> {path}")
    return {"success": True, "path": path, "count": len(rows), "requested": limit,
            "sources": sources, "note": " ".join(notes), "error": None}


def collect_and_describe(query: str, place: str, limit: int = 25) -> dict:
    """Обёртка для chat(): собирает и формирует текст ответа пользователю."""
    res = collect_recipients(query, place, limit)
    if not res["success"]:
        return {"success": False, "count": 0, "path": None,
                "reply": f"Не удалось собрать: {res['error']}"}

    per_source = ", ".join(
        f"{s['name']}: {s['added']}" for s in res["sources"] if s.get("added")
    )
    reply = f"Собрано организаций: {res['count']}"
    if res["count"] < res["requested"]:
        reply += f" из запрошенных {res['requested']}"
    if per_source:
        reply += f" ({per_source})"
    reply += ". Таблица готова."
    if res.get("note"):
        reply += f"\n\n{res['note']}"

    return {"success": True, "count": res["count"], "path": res["path"], "reply": reply}


# ==============================
# ИНСТРУМЕНТ ДЛЯ АГЕНТА
# ==============================

try:
    from langchain.tools import tool
except ImportError:                     # чтобы модуль импортировался и без langchain
    def tool(fn):
        return fn


@tool
def collect_recipients_tool(query: str, place: str, limit: int = 25) -> str:
    """
    Собирает список КОММЕРЧЕСКИХ организаций с почтой и реквизитами и пишет таблицу.

    ЭТО ОСНОВНОЙ ИНСТРУМЕНТ для запросов вида «найди <кого> в <где>»:
    автосервисы, кафе, строительные магазины, клиники, салоны, магазины дверной
    фурнитуры и любые другие коммерческие организации.

    Опрашивает источники каскадом, пока не наберёт нужное количество:
      1) 2ГИС + Checko — работает по КОНКРЕТНОМУ ГОРОДУ ИЛИ РАЙОНУ
         («Колпино», «Зеленоград»), даёт мало строк, но проверенных адресом;
      2) реестр по ОКВЭД — широкий добор, включается только если указан
         субъект РФ («Москва», «Московская область»).
    В таблицу попадают ТОЛЬКО организации с email. Дубли по ИНН убираются.

    НЕ используй для администраций МО, министерств, департаментов, комитетов и
    прочих органов власти — для них есть build_region_mo_file_tool и batch_search_tool.

    Параметры:
      query: что ищем, без географии — «автосервисы», «кафе», «дверная фурнитура»
      place: город, район или субъект РФ — «Колпино», «Москва», «Московская область»
      limit: сколько организаций нужно (по умолчанию 25)
    """
    # ЗАЩИТА ОТ НЕВЕРНОГО МАРШРУТА.
    # Промпт запрещает звать этот инструмент для органов власти, но модель
    # правило игнорирует. Поэтому отказ зашит в сам инструмент: коллектор ищет
    # по ОКВЭД и карте, а у администраций нет коммерческого ОКВЭД — на выходе
    # был бы мусор или пусто.
    gov = _detect_government(query)
    if gov:
        return (
            f"ОТКАЗ: «{query}» — это органы власти ({gov}), а не коммерческие "
            f"организации. Этот инструмент для них не подходит.\n"
            f"Используй другой маршрут:\n"
            f"  • нужен список всех МО региона -> build_region_mo_file_tool(region), "
            f"затем batch_search_tool для дозаполнения;\n"
            f"  • нужны конкретные ведомства (министерство, департамент, комитет, "
            f"управление) -> checko_search_tool(название + регион), затем "
            f"checko_company_tool(ИНН) по каждому, затем write_excel_tool."
        )

    res = collect_recipients(query, place, limit)
    if not res["success"]:
        return f"Не удалось собрать: {res['error']}"

    per_source = ", ".join(
        f"{s['name']}: {s['added']}" for s in res["sources"] if s.get("added")
    )
    out = f"Собрано организаций: {res['count']}"
    if res["count"] < res["requested"]:
        out += f" из запрошенных {res['requested']}"
    if per_source:
        out += f" ({per_source})"
    out += ". Таблица записана и готова для скачивания."
    if res.get("note"):
        out += f" {res['note']}"
    return out