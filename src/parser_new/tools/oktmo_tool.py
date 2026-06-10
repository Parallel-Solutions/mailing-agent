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

import re
import time
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
_STOP = {"муниципальные","образования","область","области","край","края","крае",
         "республика","республики","округ","округа","автономный","автономной",
         "автономная","город","города","федерального","значения","столицы",
         "российской","федерации"}
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


def resolve_region(query: str) -> dict | None:
    """
    Находит регион в индексе ОКТМО по названию (в любой форме/падеже).
    Returns: {code, url, name} или None.
    """
    idx = _get_region_index()
    qkw = _keywords(query)
    if not qkw:
        return None
    best, best_score = None, 0
    for item in idx:
        score = 0
        for q in qkw:
            for k in item["kw"]:
                if q == k or q.startswith(k) or k.startswith(q):
                    score += 1
                    break
        if score > best_score:
            best, best_score = item, score
    return best if best_score > 0 else None

def _split_name_center(s: str) -> tuple[str, str]:
    """«Ленинградский муниципальный округ (ст-ца Ленинградская)» → (название, центр)."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", s.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s.strip(), ""


def _children(url: str) -> list[dict]:
    """Прямые дети узла на classinform: [{oktmo, name, center, url}].
    Каждая ссылка дублируется (код + название); родитель помечен ведущим '-' — отсеиваем."""
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




def fetch_region_mo_list(region: str) -> tuple[list[dict], dict]:
    """
    По названию региона возвращает (список МО, инфо_о_регионе).
    Регион резолвится по индексу ОКТМО — никаких кодов ФНS.
    """
    info = resolve_region(region)
    if not info:
        raise ValueError(
            f"Регион '{region}' не найден в классификаторе ОКТМО. "
            f"Проверь название (например: 'Челябинская область', 'Забайкальский край')."
        )

    logger.info(f"[oktmo] регион распознан: {info['name']} (ОКТМО {info['code']})")
    acc: list[dict] = []
    visited: set[str] = set()
    _collect(info["url"], depth=0, acc=acc, visited=visited)

    seen, unique = set(), []
    for r in acc:
        if r["oktmo"] not in seen:
            seen.add(r["oktmo"])
            unique.append(r)

    logger.info(f"[oktmo] {info['name']}: найдено {len(unique)} действующих МО")
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


def build_region_mo_file(region: str) -> tuple[str, int, str]:
    """
    Собирает все МО региона и создаёт Excel в формате data.xlsx.
    Заполняет SUB_RF, MUN_NAME, REQUISITES_OKTMO; остальное — пусто.
    Returns: (путь_к_файлу, кол-во_МО, распознанное_название_региона).
    """
    if not _EXCEL_HELPERS:
        raise RuntimeError("excel_tool недоступен — запусти внутри проекта parser_new")

    _emit("Ищу список МО региона в классификаторе ОКТМО…")
    mo, info = fetch_region_mo_list(region)
    if not mo:
        raise ValueError(f"По региону '{region}' не найдено действующих МО.")

    # название субъекта из заголовка ОКТМО -> именительный падеж
    raw = re.sub(r"^муниципальные образования\s+", "", info["name"], flags=re.I)
    sub_rf = _subject_to_nominative(raw)
    _emit(f"Нашёл список: {sub_rf} — {len(mo)} действующих МО.")

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
def build_region_mo_file_tool(region: str) -> str:
    """
    ПЕРВЫЙ ШАГ при запросе собрать данные по ВСЕМ МО региона
    ("найди все МО Челябинской области", "собери все округа ..." и т.п.).

    Находит в официальном классификаторе ОКТМО полный список действующих
    муниципальных образований региона (по названию региона) и создаёт готовый
    Excel-файл с заполненными SUB_RF, MUN_NAME и ОКТМО.

    После этого ОБЯЗАТЕЛЬНО вызови batch_search_tool с полученным путём —
    он заполнит реквизиты, контакты и главу по каждому МО.

    Параметры:
      region: название региона ("Челябинская область", "Забайкальский край").
    """
    try:
        path, n, sub_rf = build_region_mo_file(region)
        return (f"Регион: {sub_rf}. Действующих МО: {n} (источник: ОКТМО).\n"
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