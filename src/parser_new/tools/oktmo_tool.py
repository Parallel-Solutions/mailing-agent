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


BASE = "https://classifikators.ru/oktmo"
INDEX_URL = "https://classifikators.ru/oktmo"
REQUEST_DELAY = 0.4          # вежливая задержка между запросами, сек
REQUEST_TIMEOUT = 20.0
MAX_DEPTH = 5                # страховка от зацикливания

# Строка HTML-таблицы классификатора (формат сайта):
# <a href="/oktmo/КОД">код</a></td> <td>наименование</td> <td>центр</td> <td>раздел</td>
# Используется и для индекса регионов, и для подстраниц — формат одинаковый.
_ROW_RE = re.compile(
    r'<a\s+href="/oktmo/(\d+)"[^>]*>[\d\s]+</a>\s*</td>\s*'
    r'<td[^>]*>(.*?)</td>\s*'
    r'<td[^>]*>(.*?)</td>\s*'
    r'<td[^>]*>(\d+)</td>',
    re.DOTALL,
)


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
    """Загружает и кэширует индекс регионов с главной страницы ОКТМО."""
    global _region_index
    if _region_index is not None:
        return _region_index

    page = _fetch(INDEX_URL)
    idx = []
    for row in _parse_children(page):
        name = row["name"]
        # в индексе строки региона начинаются с "Муниципальные образования ..."
        if "муниципальные образования" not in name.lower():
            continue
        idx.append({
            "code": row["oktmo"][:2],
            "url": row["url"],
            "name": name,
            "kw": _keywords(name),
        })
    _region_index = idx
    logger.info(f"[oktmo] индекс регионов загружен: {len(idx)} субъектов")
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


def _parse_children(page_text: str) -> list[dict]:
    """Дочерние коды из HTML-таблицы (только строки со ссылками)."""
    out = []
    for code, name, center, section in _ROW_RE.findall(page_text):
        out.append({
            "oktmo": code,
            "name": _clean_html(name),
            "center": _clean_html(center),
            "url": f"{BASE}/{code}",
            "section": section.strip(),
        })
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
    if "городск" in g and "округ" in g:
        suffix = "городской округ"
    elif "муниципальн" in g and "округ" in g:
        suffix = "муниципальный округ"
    elif "муниципальн" in g and "район" in g:
        suffix = "муниципальный район"
    elif "сельск" in g and "поселени" in g:
        suffix = "сельское поселение"
    elif "городск" in g and "поселени" in g:
        suffix = "городское поселение"

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
    """
    Обходит дерево ОКТМО. Конечные МО → в acc.

    Ключевой принцип: спускаемся ТОЛЬКО через узлы-контейнеры
    ("Городские округа", "Муниципальные районы" и т.п.). Прямой ребёнок
    контейнера = конечное МО — его добавляем и ВНУТРЬ НЕ ЛЕЗЕМ
    (даже если у него есть внутригородские районы или населённые пункты).

    Исключение: ветка "Муниципальные районы" — её дети (районы) сами являются
    контейнерами поселений, поэтому через них спускаемся ещё на уровень.
    """
    if depth > MAX_DEPTH or url in visited:
        return
    visited.add(url)
    try:
        page = _fetch(url)
    except Exception as e:
        logger.warning(f"[oktmo] не удалось загрузить {url}: {e}")
        return

    section1 = [c for c in _parse_children(page) if c["section"] == "1"]
    if not section1:
        return

    for child in section1:
        cname = child["name"]

        if _is_group_container(cname):
            # это уровень-контейнер — спускаемся, запоминая его тип как group_name
            _collect(child["url"], depth + 1, acc, visited, group_name=cname)
            continue

        # это конечное МО (округ / район / поселение)
        full_name = _ensure_mo_type(cname, group_name)

        # Муниципальный РАЙОН — особый случай: внутри него есть поселения (тоже МО).
        # Для рассылки нужны поселения, а не сам район → спускаемся в него.
        is_district = ("муниципальн" in group_name.lower() and "район" in group_name.lower()) \
                      or ("район" in full_name.lower() and "округ" not in full_name.lower())
        if is_district:
            # проверим, есть ли внутри поселения раздела 1
            try:
                child_page = _fetch(child["url"])
                inner = [c for c in _parse_children(child_page) if c["section"] == "1"]
            except Exception:
                inner = []
            inner_settlements = [c for c in inner if _is_group_container(c["name"])
                                 or "поселени" in c["name"].lower() or "сельсовет" in c["name"].lower()]
            if inner_settlements:
                _collect(child["url"], depth + 1, acc, visited, group_name=cname)
                continue
            # район без поселений (редко) — добавляем сам район как МО

        acc.append({
            "oktmo": child["oktmo"][:8],   # 8-значный код МО
            "name": full_name,
            "center": child["center"],
        })
        logger.debug(f"[oktmo] +МО: {full_name} ({child['oktmo'][:8]})")


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
if __name__ == "__main__":
    sample = '''
<tr> <td class="td-code"><a href="/oktmo/75503000000">75 503 000</a></td> <td>Агаповский муниципальный округ</td> <td>с Агаповка</td> <td>1</td> </tr>
<tr> <td class="td-code">75 503 000 000</td> <td>Населенные пункты</td> <td>&mdash;</td> <td>2</td> </tr>
<tr> <td class="td-code"><a href="/oktmo/75503000101">75 503 000 101</a></td> <td>с Агаповка</td> <td>&mdash;</td> <td>2</td> </tr>
'''
    parsed = _parse_children(sample)
    s1 = [c for c in parsed if c["section"] == "1"]
    assert len(s1) == 1 and s1[0]["oktmo"] == "75503000000", f"парсер сломан: {s1}"
    assert s1[0]["name"] == "Агаповский муниципальный округ"
    # резолвер на оффлайн-индексе (HTML)
    index_html = '''
<tr> <td class="td-code"><a href="/oktmo/75000000000">75 000 000</a></td> <td>Муниципальные образования Челябинской области</td> <td>&mdash;</td> <td>1</td> </tr>
<tr> <td class="td-code"><a href="/oktmo/76000000000">76 000 000</a></td> <td>Муниципальные образования Забайкальского края</td> <td>&mdash;</td> <td>1</td> </tr>
<tr> <td class="td-code"><a href="/oktmo/74000000000">74 000 000</a></td> <td>Муниципальные образования Херсонской области</td> <td>г Херсон</td> <td>1</td> </tr>
'''
    _region_index = []
    for row in _parse_children(index_html):
        if "муниципальные образования" in row["name"].lower():
            _region_index.append({"code": row["oktmo"][:2], "url": row["url"],
                                  "name": row["name"], "kw": _keywords(row["name"])})
    assert len(_region_index) == 3, f"индекс: {len(_region_index)}"
    assert resolve_region("Челябинская область")["code"] == "75"
    assert resolve_region("челябинская обл")["code"] == "75"
    assert resolve_region("Забайкальский край")["code"] == "76"

    # проверка дописывания типа МО
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

    # падеж субъекта: родительный -> именительный
    assert _subject_to_nominative("Челябинской области") == "Челябинская область"
    assert _subject_to_nominative("Забайкальского края") == "Забайкальский край"
    assert _subject_to_nominative("Московской области") == "Московская область"
    assert _subject_to_nominative("Республики Татарстан") == "Республика Татарстан"

    print("OK — все проверки прошли:")
    print("  • HTML-парсер, индекс, резолвер")
    print("  • дописывание типа МО ('Челябинский' -> 'Челябинский городской округ')")
    print("  • контейнеры vs конечные МО (внутригородские районы не попадут в список)")
    print("  • падеж субъекта ('Челябинской области' -> 'Челябинская область')")