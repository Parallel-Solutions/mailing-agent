"""
tools/oktmo_tool.py — сбор списка действующих МО региона из ОКТМО
и создание готового Excel-файла для пакетной обработки.

Источник: classifikators.ru/oktmo

ВАЖНО про коды регионов:
  Коды ОКТМО НЕ совпадают с кодами ФНС/ГИБДД из tools/regions.py!
  Пример: Челябинская область — код 74 в regions.py (ФНС), но 75 в ОКТМО.
  Поэтому регион резолвится НЕ по regions.py, а по названию прямо с индексной
  страницы ОКТМО (/oktmo). Это устраняет любые расхождения кодов и не требует
  хранить таблицу соответствий. regions.py при этом не трогаем — он нужен Checko.

Логика обхода (проверена на реальных данных):
  - узел РАЗДЕЛА 1 без дочерних кодов раздела 1  → конечное МО (лист), записываем
  - узел РАЗДЕЛА 1 с дочерними кодами раздела 1  → группировка, углубляемся
  - раздел 2 (населённые пункты) игнорируем — это не МО

Цепочка использования агентом:
  build_region_mo_file_tool("Челябинская область")
    → находит регион в индексе ОКТМО по названию
    → рекурсивно собирает все действующие МО
    → создаёт Excel (SUB_RF, MUN_NAME, ОКТМО заполнены)
    → возвращает путь
  → агент вызывает batch_search_tool(путь) для сбора реквизитов
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from openpyxl import Workbook

# --- логгер с fallback ---
try:
    from src.parser_new.logger import logger
except ImportError:
    try:
        from logger import logger
    except ImportError:
        import logging
        logger = logging.getLogger("oktmo_tool")

try:
    from src.parser_new.progress import emit as _emit
except Exception:
    def _emit(*a, **k):
        pass

# --- хелперы создания файла МО (точный формат с двойной шапкой) ---
try:
    from src.parser_new.tools.excel_tool import (
        _create_mo_header, _write_mo_row, _save_file, _make_filename,
    )
    _EXCEL_HELPERS = True
except ImportError:
    _EXCEL_HELPERS = False


BASE = "https://classinform.ru/oktmo"
INDEX_URL = "https://classinform.ru/oktmo/kod.html"
REQUEST_DELAY = 0.4          # вежливая задержка между запросами, сек
REQUEST_TIMEOUT = 20.0
MAX_DEPTH = 5                # страховка от зацикливания


def _clean_html(s: str) -> str:
    """Снимает HTML-теги и html-сущности из ячейки таблицы."""
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&mdash;", "—").replace("&nbsp;", " ").replace("&amp;", "&")
    return s.strip()

# Стоп-слова и усечение окончаний — для резолва региона по названию
# ВАЖНО: здесь должны быть ВСЕ падежные формы служебных слов. Раньше в списке
# были только "автономный"/"округ", поэтому запрос «Ханты-Мансийском автономном
# округе» матчился с ЧУКОТСКИМ автономным округом по общим словам — молчаливо
# неверный регион, что хуже пустого результата.
_STOP = {"муниципальные","муниципальных","муниципальными","муниципальное",
         "образования","образованиях","образованиями","образование",
         "область","области","областью","областей","областная",
         "край","края","крае","краем","краю",
         "республика","республики","республике","республикой","республику",
         "округ","округа","округе","округом","округу","округов",
         "автономный","автономной","автономная","автономном","автономного",
         "автономному","автономным","автономное",
         "город","города","городе","городом","городской",
         "федерального","федеральная","территория","значения","столицы",
         "российской","федерации","субъект","субъекта","регион","региона",
         "регионе"}
_SUFS = sorted(["овского","евского","ского","ской","ская","ское","инская","инской",
                "ный","ной","ная","ное","ого","его","ой","ай","ая","ое","ие","ий",
                "ы","и","а","я","о","е","й"], key=len, reverse=True)


def _fetch(url: str) -> str:
    """Загружает страницу классификатора (markdown/html-текст)."""
    time.sleep(REQUEST_DELAY)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; mo-parser/1.0)"}
    resp = httpx.get(url, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _stem(word: str) -> str:
    word = word.lower()
    for suf in _SUFS:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[:len(word) - len(suf)]
    return word


def _keywords(name: str) -> set[str]:
    words = re.sub(r"[()]", " ", name.lower()).split()
    return {_stem(w) for w in words if w not in _STOP and len(w) > 2}


# --- индекс регионов (кэшируется в памяти процесса) ---
_region_index: list[dict] | None = None


def _get_region_index() -> list[dict]:
    """Список субъектов с classinform (индекс kod.html). Кэшируется."""
    global _region_index
    if _region_index is not None:
        return _region_index
    idx = []
    for ch in _children(INDEX_URL):
        code = ch["oktmo"]
        if not (len(code) == 11 and code.endswith("000000000")):
            continue  # только субъекты: RR + 9 нулей
        idx.append({
            "code": code[:2], "oktmo": code, "url": f"{BASE}/{code}.html",
            "name": ch["name"], "kw": _keywords(ch["name"]),
        })
    _region_index = idx
    logger.info(f"[oktmo] индекс регионов (classinform): {len(idx)} субъектов")
    return idx


# --- субъекты, которых НЕТ в индексе классификатора ---
# Автономные округа в ОКТМО вложены в родительский субъект, поэтому на
# странице-индексе (88 записей) их нет вовсе: ХМАО и ЯНАО лежат под Тюменской
# областью (71), Ненецкий АО — под Архангельской (11). Их коды не проходят
# фильтр «код региона + 9 нулей», и раньше такие запросы либо не находились,
# либо матчились с чужим округом. Здесь они заданы прямыми кодами ОКТМО.
#
# Порядок важен: «Ямало-Ненецкий» содержит «ненецк», поэтому ямальские ключи
# проверяются раньше ненецких.
_NESTED_SUBJECTS = [
    (("ямал", "янао", "ямало-ненецк"), "71900000000",
     "Ямало-Ненецкий автономный округ"),
    (("ханты", "хмао", "югра", "югры", "югре"), "71800000000",
     "Ханты-Мансийский автономный округ — Югра"),
    (("ненецк", "нао"), "11800000000",
     "Ненецкий автономный округ"),
]

# Сокращения и разговорные написания: по корням слов они не сматчатся никогда
# («хмао» не является префиксом «ханты-мансийск» и наоборот).
_ALIASES = {
    "башкирия": "Башкортостан", "татария": "Татарстан", "якутия": "Саха",
    "чувашия": "Чувашская", "удмуртия": "Удмуртская", "мордовия": "Мордовия",
    "кчр": "Карачаево-Черкесская", "кбр": "Кабардино-Балкарская",
    "рсо": "Северная Осетия", "алания": "Северная Осетия",
    "спб": "Санкт-Петербург", "питер": "Санкт-Петербург",
    "мск": "Москва", "мо": "Московская",
    "еао": "Еврейская", "чао": "Чукотский",
}


def _match_nested(query: str) -> dict | None:
    """Автономные округа, отсутствующие в индексе, — по прямому коду ОКТМО."""
    low = (query or "").lower()
    for keys, code, name in _NESTED_SUBJECTS:
        if any(k in low for k in keys):
            return {"code": code[:2], "oktmo": code,
                    "url": f"{BASE}/{code}.html", "name": name,
                    "kw": _keywords(name)}
    return None


def resolve_region(query: str) -> dict | None:
    """
    Находит регион в индексе ОКТМО по названию (в любой форме/падеже).
    Returns: {code, url, name} или None.
    """
    if not (query or "").strip():
        return None

    # 1) автономные округа: их нет в индексе, только прямым кодом
    nested = _match_nested(query)
    if nested:
        logger.info(f"[oktmo] '{query}' -> {nested['name']} (вложенный округ)")
        return nested

    # 2) сокращения -> развёрнутое название
    q = query
    low = q.lower()
    for alias, full in _ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", low):
            q = f"{q} {full}"
            break

    idx = _get_region_index()
    qkw = _keywords(q)
    if not qkw:
        return None

    best, best_score = None, 0
    for item in idx:
        score = 0
        for qk in qkw:
            for k in item["kw"]:
                # односимвольные пересечения не считаем: они дают ложные матчи
                if qk == k or (len(k) >= 4 and qk.startswith(k)) \
                        or (len(qk) >= 4 and k.startswith(qk)):
                    score += 1
                    break
        if score > best_score:
            best, best_score = item, score

    if not best:
        logger.info(f"[oktmo] регион не распознан: {query!r} (ключи: {sorted(qkw)})")
        return None

    logger.info(f"[oktmo] '{query}' -> {best['name']} (совпадений: {best_score})")
    return best

def _split_name_center(s: str) -> tuple[str, str]:
    """«Ленинградский муниципальный округ (ст-ца Ленинградская)» → (название, центр)."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", s.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s.strip(), ""


# Страницы классификатора в пределах запуска не меняются, а _collect заходит на
# страницу района ДВАЖДЫ: сперва чтобы проверить наличие поселений, потом чтобы
# в неё спуститься. Без кэша это удваивает число сетевых запросов.
_children_cache: dict[str, list[dict]] = {}


def _children(url: str) -> list[dict]:
    """Прямые дети узла на classinform: [{oktmo, name, center, url}].
    Каждая ссылка дублируется (код + название); родитель помечен ведущим '-' — отсеиваем."""
    cached = _children_cache.get(url)
    if cached is not None:
        return cached
    soup = BeautifulSoup(_fetch(url), "lxml")
    by_code: dict[str, list[str]] = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/oktmo/(\d{11})\.html", a["href"])
        if m:
            by_code.setdefault(m.group(1), []).append(a.get_text(" ", strip=True))

    cur = re.search(r"/oktmo/(\d{11})\.html", url)
    cur_code = cur.group(1) if cur else ""

    out = []
    for code, texts in by_code.items():
        if code == cur_code:
            continue
        if any(t.strip().startswith("-") for t in texts):   # родитель
            continue
        name = next((t for t in texts if not re.fullmatch(r"[\d\s]+", t)), "")
        if not name:
            continue
        nm, center = _split_name_center(name)
        out.append({"oktmo": code, "name": nm, "center": center, "url": f"{BASE}/{code}.html"})
    _children_cache[url] = out
    return out


def _ensure_mo_type(name: str, group_name: str) -> str:
    """
    Гарантирует, что в названии МО есть тип ("городской округ" и т.п.).
    На сайте ОКТМО у части МО тип вынесен в заголовок ветки (group_name),
    а в самой строке остаётся только топоним ("Челябинский"). Дописываем тип.
    """
    name = name.strip()
    low = name.lower()
    # уже содержит любой тип МО — не трогаем
    types = ["муниципальный округ", "городской округ", "муниципальный район",
             "сельское поселение", "городское поселение", "сельсовет",
             "поссовет", "внутригородск"]
    if any(t in low for t in types):
        return name

    # определяем тип по названию ветки-группы
    g = (group_name or "").lower()
    suffix = ""
    if "поселени" in g:
        suffix = "сельское поселение" if "сельск" in g else "городское поселение"
    elif "городск" in g and "округ" in g:
        suffix = "городской округ"
    elif "муниципальн" in g and "округ" in g:
        suffix = "муниципальный округ"
    elif "муниципальн" in g and "район" in g:
        suffix = "муниципальный район"
        

    if suffix and suffix not in low:
        return f"{name} {suffix}"
    return name


def _is_group_container(name: str) -> bool:
    """
    True, если узел — это ГРУППА-контейнер (уровень иерархии), через который
    надо спускаться: "Муниципальные округа", "Городские округа",
    "Муниципальные районы", "Сельские/Городские поселения".
    False — если это само конечное МО (Агаповский муниципальный округ,
    Челябинский, Энемское сельское поселение и т.п.).
    """
    low = name.strip().lower()
    # точные названия веток-контейнеров (множественное число, без топонима)
    containers = [
        "муниципальные округа", "городские округа", "муниципальные районы",
        "сельские поселения", "городские поселения", "внутригородские районы",
        "внутригородские территории", "населенные пункты", "населённые пункты",
    ]
    return any(low == c or low.startswith(c) for c in containers)


def _collect(url: str, depth: int, acc: list[dict], visited: set[str],
             group_name: str = "") -> None:
    if depth > MAX_DEPTH or url in visited:
        return
    visited.add(url)
    for child in _children(url):
        cname = child["name"]
        if _is_group_container(cname):
            _collect(child["url"], depth + 1, acc, visited, group_name=cname)
            continue
        full_name = _ensure_mo_type(cname, group_name)
        low = full_name.lower()
        is_district = ("муниципальн" in group_name.lower() and "район" in group_name.lower()) \
                      or ("район" in low and "округ" not in low)
        if is_district:
            inner = _children(child["url"])
            settlements = [c for c in inner if _is_group_container(c["name"])
                           or "поселени" in c["name"].lower() or "сельсовет" in c["name"].lower()]
            if settlements:
                _collect(child["url"], depth + 1, acc, visited, group_name=cname)
                continue
        acc.append({"oktmo": child["oktmo"][:8], "name": full_name, "center": child["center"]})




# ==============================
# ДИСКОВЫЙ КЭШ СПИСКА МО
# ==============================
#
# Обход дерева классификатора занимает 1-3 минуты на регион (десятки страниц с
# вежливой задержкой). При этом состав муниципалитетов меняется в результате
# реформ — раз в несколько месяцев, не чаще. Поэтому список кэшируется на диск.

_MO_CACHE_TTL_SEC = 30 * 24 * 3600      # месяц


def _mo_cache_dir() -> Path | None:
    try:
        try:
            from src.parser_new import config
        except ImportError:
            import config
        d = Path(config.MEMORY_DIR) / "oktmo"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception as e:
        logger.debug(f"[oktmo] кэш недоступен: {e}")
        return None


def _mo_cache_load(oktmo: str) -> list[dict] | None:
    d = _mo_cache_dir()
    if not d:
        return None
    f = d / f"{oktmo}.json"
    if not f.exists():
        return None
    try:
        if time.time() - f.stat().st_mtime > _MO_CACHE_TTL_SEC:
            logger.info(f"[oktmo] кэш устарел, обновляю: {f.name}")
            return None
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) and data else None
    except Exception as e:
        logger.warning(f"[oktmo] не удалось прочитать кэш {f.name}: {e}")
        return None


def _mo_cache_save(oktmo: str, rows: list[dict]) -> None:
    d = _mo_cache_dir()
    if not d or not rows:
        return
    try:
        (d / f"{oktmo}.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[oktmo] не удалось сохранить кэш: {e}")


def fetch_region_mo_list(region: str, refresh: bool = False) -> tuple[list[dict], dict]:
    """
    По названию региона возвращает (список МО, инфо_о_регионе).
    Регион резолвится по индексу ОКТМО — никаких кодов ФНС.

    refresh=True — игнорировать кэш и перечитать классификатор.
    """
    info = resolve_region(region)
    if not info:
        raise ValueError(
            f"Регион '{region}' не найден в классификаторе ОКТМО. "
            f"Проверь название (например: 'Челябинская область', 'Забайкальский край')."
        )

    logger.info(f"[oktmo] регион распознан: {info['name']} (ОКТМО {info['code']})")

    key = str(info.get("oktmo") or info.get("code") or region)
    if not refresh:
        cached = _mo_cache_load(key)
        if cached:
            logger.info(f"[oktmo] {info['name']}: {len(cached)} МО из кэша "
                        f"(обход классификатора пропущен)")
            _emit(f"Беру список МО из сохранённого справочника ({len(cached)}).")
            return cached, info

    _emit("Обхожу классификатор ОКТМО, это занимает пару минут…")
    acc: list[dict] = []
    visited: set[str] = set()
    _collect(info["url"], depth=0, acc=acc, visited=visited)

    seen, unique = set(), []
    for r in acc:
        if r["oktmo"] not in seen:
            seen.add(r["oktmo"])
            unique.append(r)

    logger.info(f"[oktmo] {info['name']}: найдено {len(unique)} действующих МО")
    _mo_cache_save(key, unique)
    return unique, info


def _subject_to_nominative(genitive: str) -> str:
    """
    Приводит название субъекта из родительного падежа (как в заголовке ОКТМО
    "Муниципальные образования Челябинской области") к именительному
    ("Челябинская область") для столбца SUB_RF.
    """
    s = genitive.strip()
    low = s.lower()

    # "области" -> "область" + прилагательное в ж.р. им.п.
    if low.endswith("области"):
        adj = s[: -len("области")].strip()
        adj = re.sub(r"ой$", "ая", adj)   # Челябинской -> Челябинская
        adj = re.sub(r"ей$", "яя", adj)
        return f"{adj} область"
    # "края" -> "край" + прилагательное в м.р. им.п.
    if low.endswith("края"):
        adj = s[: -len("края")].strip()
        adj = re.sub(r"ого$", "ий", adj)   # Забайкальского -> Забайкальский
        adj = re.sub(r"его$", "ий", adj)
        return f"{adj} край"
    # "Республики X" -> "Республика X"
    if low.startswith("республики"):
        return "Республика" + s[len("Республики"):]
    # "автономного округа" -> "автономный округ"
    if low.endswith("автономного округа"):
        adj = s[: -len("автономного округа")].strip()
        adj = re.sub(r"ого$", "ый", adj)
        return f"{adj} автономный округ".strip()
    # "автономной области" -> "автономная область"
    if low.endswith("автономной области"):
        adj = s[: -len("автономной области")].strip()
        adj = re.sub(r"ой$", "ая", adj)
        return f"{adj} автономная область".strip()
    # города федерального значения: "города Москвы" -> "Москва" и т.п. — оставляем как есть
    return s


# Тип МО по слову из запроса пользователя. В ОКТМО названия выглядят как
# «городской округ Сургут», «Ханты-Мансийский муниципальный район»,
# «сельское поселение Луговской» — фильтруем по вхождению.
# Правила проверяются ПО ПОРЯДКУ, от частного к общему: «городские округа»
# должны сработать раньше, чем просто «округа» или «городские».
# Слева — что должно встретиться в запросе (все элементы), справа — подстроки
# для отбора по названию МО.
_MO_TYPE_RULES = (
    (("городск", "округ"),     ("городской округ",)),
    (("городск", "поселени"),  ("городское поселение",)),
    (("муниципальн", "округ"), ("муниципальный округ",)),
    (("муниципальн", "район"), ("муниципальный район",)),
    (("сельск",),              ("сельское поселение",)),
    (("город",),               ("городской округ",)),
    (("район",),               ("муниципальный район",)),
    (("округ",),               ("городской округ", "муниципальный округ")),
    (("поселени",),            ("сельское поселение", "городское поселение")),
)


def _mo_type_patterns(mo_type: str) -> tuple[str, ...]:
    """Слова из запроса -> подстроки, по которым отбираем МО. Пусто = все типы."""
    low = (mo_type or "").strip().lower()
    if not low:
        return ()
    for needles, patterns in _MO_TYPE_RULES:
        if all(n in low for n in needles):
            return patterns
    return ()


# ==============================
# ПОДСКАЗКА ИЗ ИСХОДНОЙ ФРАЗЫ ПОЛЬЗОВАТЕЛЯ
# ==============================
#
# Модель систематически вызывает build_region_mo_file_tool без mo_type, и вместо
# 13 городских округов пользователь получает все 107 МО вместе с сельскими
# поселениями. Поэтому тип и количество разбираются из исходной фразы ДО запуска
# агента и подставляются, если он параметр не передал.

_REQUEST_HINT: dict[str, object] = {"mo_type": "", "limit": 0}

# Как называть отобранные МО в сообщениях пользователю. Родительный падеж
# множественного числа — он подходит и для «список ...», и для «13 ...».
_MO_LABELS = {
    ("городской округ",): "городских округов",
    ("муниципальный округ",): "муниципальных округов",
    ("муниципальный район",): "районов",
    ("сельское поселение",): "сельских поселений",
    ("городское поселение",): "городских поселений",
    ("городской округ", "муниципальный округ"): "округов",
    ("сельское поселение", "городское поселение"): "поселений",
}


def mo_label(mo_type: str = "") -> str:
    """Как назвать искомые МО в сообщении пользователю.
    Пусто -> нейтральное «МО»; иначе — то, что человек и просил."""
    patterns = _mo_type_patterns(mo_type or str(_REQUEST_HINT.get("mo_type") or ""))
    return _MO_LABELS.get(tuple(patterns), "МО") if patterns else "МО"


def set_request_hint(message: str) -> None:
    """Запоминает тип МО и количество из фразы пользователя. Вызывается из chat()."""
    text = (message or "").strip()
    _REQUEST_HINT["mo_type"] = ""
    _REQUEST_HINT["limit"] = 0
    if not text:
        return

    # берём только «что ищем», без географии: иначе «районы» из названия места
    # («Ханты-Мансийского района») будут приняты за тип МО
    head = re.split(r"\bв\s+регионе\b|\bв\s+субъекте\b", text, maxsplit=1,
                    flags=re.IGNORECASE)[0]
    if head == text:
        parts = re.split(r"\s+в\s+", text, flags=re.IGNORECASE)
        head = parts[0] if len(parts) > 1 else text

    low = head.lower()
    for needles, _patterns in _MO_TYPE_RULES:
        if all(n in low for n in needles):
            _REQUEST_HINT["mo_type"] = head.strip()
            logger.info(f"[oktmo] тип МО из запроса: {head.strip()!r}")
            break

    m = re.search(r"объ[её]м\s*[:\-]?\s*(\d{1,4})", text, re.IGNORECASE)
    if m:
        _REQUEST_HINT["limit"] = int(m.group(1))


def build_region_mo_file(region: str, mo_type: str = "",
                         limit: int = 0) -> tuple[str, int, str]:
    """
    Собирает МО региона и создаёт Excel в формате data.xlsx.
    Заполняет SUB_RF, MUN_NAME, REQUISITES_OKTMO; остальное — пусто.

    mo_type: отбор по типу («городские округа», «районы», «сельские поселения»).
             Пусто — все типы подряд.
    limit:   сколько строк оставить. 0 — без ограничения.

    Returns: (путь_к_файлу, кол-во_МО, распознанное_название_региона).
    """
    if not _EXCEL_HELPERS:
        raise RuntimeError("excel_tool недоступен — запусти внутри проекта parser_new")

    # подсказку из фразы пользователя подставляем ДО первого сообщения, иначе
    # в интерфейсе мелькнёт нейтральное «МО» вместо запрошенного типа
    if not (mo_type or "").strip():
        mo_type = str(_REQUEST_HINT.get("mo_type") or "")
        if mo_type:
            logger.info(f"[oktmo] агент не задал mo_type, беру из запроса: {mo_type!r}")
    if not limit:
        limit = int(_REQUEST_HINT.get("limit") or 0)

    # Запоминаем ИТОГОВЫЙ тип — неважно, пришёл он параметром от агента или из
    # разбора фразы. Дальше его читает batch_processor через mo_label(), чтобы
    # в интерфейсе было «13 городских округов», а не безликое «13 МО».
    if (mo_type or "").strip():
        _REQUEST_HINT["mo_type"] = mo_type

    _emit(f"Ищу список {mo_label(mo_type)} региона в классификаторе ОКТМО…")
    mo, info = fetch_region_mo_list(region)
    if not mo:
        raise ValueError(f"По региону '{region}' не найдено действующих МО.")

    # отбор по типу: пользователь просит «администрации городов», а не всё подряд
    total_in_region = len(mo)
    patterns = _mo_type_patterns(mo_type)
    if patterns:
        before = len(mo)
        mo = [r for r in mo
              if any(p in (r.get("name") or "").lower() for p in patterns)]
        logger.info(f"[oktmo] фильтр по типу {mo_type!r}: {before} -> {len(mo)}")
        if not mo:
            raise ValueError(
                f"В регионе '{region}' не найдено МО типа '{mo_type}'. "
                f"Всего действующих МО: {before}. Уточни тип или собери все."
            )

    if limit and limit > 0:
        mo = mo[:limit]

    # название субъекта из заголовка ОКТМО -> именительный падеж
    raw = re.sub(r"^муниципальные образования\s+", "", info["name"], flags=re.I)
    sub_rf = _subject_to_nominative(raw)
    # Показываем ИТОГ отбора. Сырое число до фильтра (107 вместо 13) пользователя
    # только путает — он просил города, а не все МО региона.
    label = mo_label(mo_type)
    if len(mo) != total_in_region:
        _emit(f"{sub_rf}: отобрано {len(mo)} {label} "
              f"(всего действующих МО в регионе: {total_in_region}).")
    else:
        _emit(f"{sub_rf}: {len(mo)} действующих {label}.")

    wb = Workbook()
    ws = wb.active
    ws.title = "МО"
    _create_mo_header(ws)
    for i, r in enumerate(mo, start=1):
        _write_mo_row(ws, i + 2, {
            "ID": i,
            "SUB_RF": sub_rf,
            "MUN_NAME": r["name"],
            "REQUISITES_OKTMO": r["oktmo"],
        })

    path = _save_file(wb, _make_filename("region"))
    logger.info(f"[oktmo] файл создан: {path} ({len(mo)} МО)")
    return path, len(mo), sub_rf


# ==============================
# ИНСТРУМЕНТЫ ДЛЯ АГЕНТА
# ==============================

try:
    from langchain.tools import tool
except ImportError:
    def tool(fn): return fn

def _collect_okrugs(url, acc, visited, include_city, group_name=""):
    if url in visited:
        return
    visited.add(url)
    for child in _children(url):
        cname = child["name"]
        low = cname.lower()
        if _is_group_container(cname):
            if "округ" in low and ("муниципальн" in low or (include_city and "городск" in low)):
                _collect_okrugs(child["url"], acc, visited, include_city, group_name=cname)
            continue
        full = _ensure_mo_type(cname, group_name)
        fl = full.lower()
        if "муниципальный округ" in fl or (include_city and "городской округ" in fl):
            acc.append({"oktmo": child["oktmo"][:8], "name": full, "center": child["center"]})


def build_okrugs_file(region: str = "", include_city: bool = True) -> tuple[str, int, str]:
    """
    Округа в файл по шаблону data.xlsx. region пуст → вся Россия; иначе один субъект.
    SUB_RF (B), MUN_R_NAME (C) = округ, REQUISITES_OKTMO; D пуст.
    Returns: (путь, кол-во, охват).
    """
    if not _EXCEL_HELPERS:
        raise RuntimeError("excel_tool недоступен — запусти внутри проекта parser_new")

    if region and region.strip():
        info = resolve_region(region)
        if not info:
            raise ValueError(f"Регион '{region}' не найден в ОКТМО.")
        regions = [info]
        scope = _subject_to_nominative(re.sub(r"(?i)^муниципальные образования\s+", "", info["name"]))
    else:
        regions = _get_region_index()
        scope = "Россия"

    _emit(f"Собираю округа: {scope} ({len(regions)} субъект(ов))…")
    rows: list[tuple[str, str, str]] = []
    for n, reg in enumerate(regions, 1):
        sub_rf = _subject_to_nominative(
            re.sub(r"(?i)^муниципальные образования\s+", "", reg["name"]))
        if len(regions) > 1:
            _emit(f"[{n}/{len(regions)}] {sub_rf}…")
        try:
            acc, visited = [], set()
            _collect_okrugs(reg["url"], acc, visited, include_city)
        except Exception as e:
            logger.warning(f"[oktmo] {sub_rf}: {e}")
            continue
        seen = set()
        for m in acc:
            if m["oktmo"] in seen:
                continue
            seen.add(m["oktmo"])
            rows.append((sub_rf, m["name"], m["oktmo"]))

    if not rows:
        raise ValueError("Округа не найдены (возможно, ОКТМО недоступен).")

    wb = Workbook()
    ws = wb.active
    ws.title = "Округа"
    _create_mo_header(ws)
    for i, (sub_rf, name, oktmo) in enumerate(rows, start=1):
        _write_mo_row(ws, i + 2, {
            "ID": i, "SUB_RF": sub_rf, "MUN_R_NAME": name, "REQUISITES_OKTMO": oktmo,
        })
    path = _save_file(wb, _make_filename("okruga"))
    logger.info(f"[oktmo] округа ({scope}): {len(rows)} → {path}")
    return path, len(rows), scope


@tool
def build_okrugs_file_tool(region: str = "", include_city: bool = True) -> str:
    """
    Собирает округа в файл по шаблону.
    Регион НЕ указан → по всей России. Указан → только по этому субъекту.
    include_city=True — муниципальные И городские округа (по умолчанию);
    include_city=False — ТОЛЬКО муниципальные округа.
    Примеры:
      «все муниципальные округа России» → region="", include_city=False
      «все округа Амурской области»     → region="Амурская область", include_city=True
      «муниципальные округа Амурской области» → region="Амурская область", include_city=False
    Заполняет: субъект (B), округ (C), ОКТМО. Реквизиты НЕ ищет.
    Приложенный к чату файл к этой задаче не относится — игнорируй его.
    """
    try:
        path, n, scope = build_okrugs_file(region=region, include_city=include_city)
        kind = "муниципальные и городские" if include_city else "только муниципальные"
        return (f"Готово ({kind}). Округов ({scope}): {n} (источник: ОКТМО).\n"
                f"Файл: {path}\nЗаполнены: субъект (B), округ (C), ОКТМО.")
    except Exception as e:
        logger.error(f"[oktmo] ошибка сборки округов: {e}")
        return f"Не удалось собрать округа: {e}"

@tool
def build_region_mo_file_tool(region: str, mo_type: str = "", limit: int = 0) -> str:
    """
    ПЕРВЫЙ ШАГ при запросе собрать данные по муниципальным образованиям региона:
    администрации городов, районов, поселений, округов.

    Находит в официальном классификаторе ОКТМО список действующих МО региона и
    создаёт Excel с заполненными SUB_RF, MUN_NAME и ОКТМО.

    После этого ОБЯЗАТЕЛЬНО вызови batch_search_tool с полученным путём —
    он заполнит реквизиты, контакты и главу по каждому МО.

    Параметры:
      region:  название региона ("Челябинская область", "ХМАО", "Забайкальский край")
      mo_type: КАКИЕ ИМЕННО МО нужны. Обязательно передавай, если пользователь
               назвал тип — иначе вернутся ВСЕ МО подряд, включая сотни сельских
               поселений, и пользователь получит не то, что просил.
                 "городские округа" / "города" -> только городские округа
                 "муниципальные округа"        -> только муниципальные округа
                 "районы"                      -> только муниципальные районы
                 "сельские поселения"          -> только сельские поселения
                 ""                            -> все типы подряд
      limit:   сколько МО оставить. Если пользователь указал количество —
               передавай его сюда. 0 — без ограничения.

    Примеры:
      «администрации городов ХМАО, 20 штук»
          -> region="ХМАО", mo_type="городские округа", limit=20
      «все МО Челябинской области»
          -> region="Челябинская область", mo_type="", limit=0
      «сельские поселения Татарстана»
          -> region="Татарстан", mo_type="сельские поселения", limit=0
    """
    try:
        path, n, sub_rf = build_region_mo_file(region, mo_type=mo_type, limit=limit)
        kind = f" (тип: {mo_type})" if mo_type else ""
        return (f"Регион: {sub_rf}. Отобрано МО{kind}: {n} (источник: ОКТМО).\n"
                f"Создан файл: {path}\n"
                f"Следующий шаг: вызови batch_search_tool(file_path=\"{path}\").")
    except Exception as e:
        logger.error(f"[oktmo] ошибка: {e}")
        return f"Не удалось собрать список МО: {e}"


@tool
def oktmo_region_list_tool(region: str) -> str:
    """
    Возвращает СПИСОК действующих МО региона из ОКТМО (без создания файла).
    Используй если пользователь хочет просто посмотреть перечень МО.

    Параметры:
      region: название региона.
    """
    try:
        mo, info = fetch_region_mo_list(region)
        if not mo:
            return f"По региону '{region}' не найдено действующих МО."
        sub_rf = re.sub(r"^муниципальные образования\s+", "", info["name"], flags=re.I)
        lines = [f"{sub_rf}: действующих МО — {len(mo)} (источник: ОКТМО)"]
        for i, r in enumerate(mo, 1):
            lines.append(f"{i}. {r['name']} | ОКТМО {r['oktmo']} | центр: {r['center']}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[oktmo] ошибка: {e}")
        return f"Не удалось получить список МО: {e}"


# Самопроверка логики парсинга и резолвера
# Самопроверка (оффлайн, без сети)
if __name__ == "__main__":
    # парсер «название (центр)»
    assert _split_name_center("Ленинградский муниципальный округ (ст-ца Ленинградская)") \
        == ("Ленинградский муниципальный округ", "ст-ца Ленинградская")
    assert _split_name_center("Муниципальные округа Краснодарского края") \
        == ("Муниципальные округа Краснодарского края", "")

    # резолвер на оффлайн-индексе (подсовываем кэш напрямую, без запроса к сайту)
    _region_index = [
        {"code": "75", "oktmo": "75000000000", "url": f"{BASE}/75000000000.html",
         "name": "Муниципальные образования Челябинской области",
         "kw": _keywords("Муниципальные образования Челябинской области")},
        {"code": "76", "oktmo": "76000000000", "url": f"{BASE}/76000000000.html",
         "name": "Муниципальные образования Забайкальского края",
         "kw": _keywords("Муниципальные образования Забайкальского края")},
        {"code": "74", "oktmo": "74000000000", "url": f"{BASE}/74000000000.html",
         "name": "Муниципальные образования Херсонской области",
         "kw": _keywords("Муниципальные образования Херсонской области")},
    ]
    assert resolve_region("Челябинская область")["code"] == "75"
    assert resolve_region("челябинская обл")["code"] == "75"
    assert resolve_region("Забайкальский край")["code"] == "76"

    # дописывание типа МО
    assert _ensure_mo_type("Челябинский", "Городские округа") == "Челябинский городской округ"
    assert _ensure_mo_type("Копейский", "Городские округа") == "Копейский городской округ"
    assert _ensure_mo_type("Агаповский муниципальный округ", "Муниципальные округа") == "Агаповский муниципальный округ"
    assert _ensure_mo_type("Ивановское", "Сельские поселения") == "Ивановское сельское поселение"

    # контейнеры vs конечные МО
    assert _is_group_container("Городские округа") is True
    assert _is_group_container("Муниципальные округа") is True
    assert _is_group_container("Внутригородские районы городского округа Челябинский") is True
    assert _is_group_container("Челябинский") is False
    assert _is_group_container("Агаповский муниципальный округ") is False

    # падеж субъекта
    assert _subject_to_nominative("Челябинской области") == "Челябинская область"
    assert _subject_to_nominative("Забайкальского края") == "Забайкальский край"

    print("OK — проверки пройдены: _split_name_center, resolve_region, "
          "_ensure_mo_type, _is_group_container, _subject_to_nominative")