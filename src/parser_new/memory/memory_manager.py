"""
memory/memory_manager.py — единый интерфейс ко всей памяти агента.

Снаружи весь проект работает только с этим файлом.
Он сам решает в какую базу что писать и откуда читать.

Также здесь живут инструменты агента для работы с памятью —
агент сам решает когда запомнить правило или ошибку.
"""
from __future__ import annotations

from langchain.tools import tool

from src.parser_new.memory.sqlite_memory  import (
    init_db, add_rule, get_rules, remember_error,
    get_recent_errors, update_stats, log_run,
    get_context_for_url as _sql_context,
)
from src.parser_new.memory.vector_memory  import (
    remember_experience, find_similar,
    get_semantic_context,
)
from src.parser_new.memory.cache_memory   import (
    get_cached_url, set_cached_url,
    check_rate_limit,
    session_add_processed, session_is_processed,
)
from src.parser_new.logger import logger


# ==============================
# ИНИЦИАЛИЗАЦИЯ
# ==============================

def init_memory() -> None:
    """Инициализирует все хранилища. Вызывается один раз при старте."""
    init_db()
    logger.info("[memory] Память инициализирована")


# ==============================
# ГЛАВНАЯ ФУНКЦИЯ — контекст перед задачей
# ==============================

def get_full_context(url: str = "", situation: str = "") -> str:
    """
    Собирает полный контекст из всех трёх баз памяти.
    Вызывается перед каждой задачей — агент читает накопленный опыт.

    Args:
        url:       URL который собираемся парсить (для SQLite)
        situation: описание текущей задачи (для ChromaDB)
    """
    parts = []

    # 1. Структурированные правила и ошибки из SQLite
    if url:
        sql_ctx = _sql_context(url)
        if sql_ctx:
            parts.append(sql_ctx)

    # 2. Похожие ситуации из ChromaDB
    if situation:
        vec_ctx = get_semantic_context(situation)
        if vec_ctx:
            parts.append(vec_ctx)

    if not parts:
        return ""

    return "\n\n".join(parts)


# ==============================
# ИНСТРУМЕНТЫ ДЛЯ АГЕНТА
# ==============================

@tool
def memory_add_rule_tool(domain: str, rule_type: str, rule_value: str) -> str:
    """
    Запоминает рабочее правило для домена.
    Используй когда нашёл способ который помог при работе с сайтом.

    Параметры:
      domain:     домен сайта (например 'site.ru')
      rule_type:  тип правила — одно из:
                  'header'   — нужен особый HTTP заголовок
                  'delay'    — нужна задержка между запросами (в секундах)
                  'selector' — CSS-селектор для нужного элемента
                  'browser'  — нужен браузерный парсер (Playwright)
                  'skip'     — этот источник недоступен, пропустить
                  'note'     — произвольная заметка
      rule_value: значение правила (например 'User-Agent: Mozilla/5.0')
    """
    try:
        add_rule(domain, rule_type, rule_value)
        return f"✅ Правило запомнено: [{rule_type}] {rule_value} для {domain}"
    except Exception as e:
        return f"Не удалось сохранить правило: {e}"


@tool
def memory_remember_error_tool(
    url: str,
    tool_name: str,
    error_type: str,
    error_detail: str,
    solution: str = "",
) -> str:
    """
    Записывает ошибку и её решение в память.
    Используй ВСЕГДА когда столкнулся с ошибкой при работе с источником.

    Параметры:
      url:          URL где произошла ошибка
      tool_name:    какой инструмент использовался (scraper_tool, search_tool и т.д.)
      error_type:   тип ошибки — одно из:
                    'timeout'      — сайт не ответил
                    'blocked'      — сайт заблокировал (403, капча)
                    'empty_page'   — страница пустая
                    'parse_fail'   — не удалось извлечь нужные данные
                    'not_found'    — данные не найдены
                    'api_error'    — ошибка API
      error_detail: подробное описание что пошло не так
      solution:     что помогло решить проблему (оставь пустым если не решено)
    """
    try:
        remember_error(url, tool_name, error_type, error_detail, solution)
        msg = f"✅ Ошибка записана: {error_type} @ {url}"
        if solution:
            msg += f" | Решение: {solution}"
        return msg
    except Exception as e:
        return f"Не удалось записать ошибку: {e}"


@tool
def memory_save_experience_tool(
    situation: str,
    solution: str,
    outcome: str,
    domain: str = "",
    tool_used: str = "",
) -> str:
    """
    Сохраняет опыт в семантическую память (ChromaDB).
    Используй когда завершил сложную задачу — опиши что было и что помогло.
    Этот опыт поможет агенту при похожих задачах в будущем.

    Параметры:
      situation: опиши проблему или ситуацию своими словами
      solution:  что именно было сделано для решения
      outcome:   'success' если сработало, 'fail' если нет
      domain:    домен сайта если применимо (необязательно)
      tool_used: какой инструмент помог (необязательно)
    """
    try:
        remember_experience(situation, solution, outcome, domain, tool_used)
        icon = "✅" if outcome == "success" else "❌"
        return f"{icon} Опыт сохранён в семантическую память"
    except Exception as e:
        return f"Не удалось сохранить опыт: {e}"


@tool
def memory_get_context_tool(url: str, situation: str = "") -> str:
    """
    Загружает накопленный опыт о конкретном сайте или задаче.
    Используй В НАЧАЛЕ работы с новым источником —
    агент узнает что уже известно об этом сайте и какие правила работают.

    Параметры:
      url:       URL сайта с которым предстоит работать
      situation: краткое описание задачи (для поиска похожего опыта)
    """
    try:
        context = get_full_context(url=url, situation=situation)
        if not context:
            return f"Память о {url} пуста — работаем с чистого листа."
        return context
    except Exception as e:
        return f"Не удалось загрузить контекст: {e}"


# ==============================
# ПРОКСИ-ФУНКЦИИ ДЛЯ КЭША
# (используются инструментами напрямую, не через агента)
# ==============================

def try_cache(url: str) -> dict | None:
    """Проверяет есть ли закэшированный результат для URL."""
    return get_cached_url(url)


def save_cache(url: str, result: dict) -> None:
    """Кэширует результат парсинга."""
    set_cached_url(url, result)


def can_request(domain: str) -> bool:
    """Проверяет не превышен ли rate limit для домена."""
    return check_rate_limit(domain)
