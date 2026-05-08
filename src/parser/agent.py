"""
Агент-парсер МО.

Принимает сообщения от пользователя, решает какие инструменты вызвать,
выполняет их и возвращает ответ. Хранит память в data/agent_memory.json.
"""

import json
from pathlib import Path

from openai import OpenAI

from src.jobs import resolve_job_paths
from src.parser.tools import TOOL_DEFINITIONS, call_tool, configure_parser_paths
from src.utils.config import settings
from src.utils.logger import logger


# ------------------------------------------------------------------
# Константы
# ------------------------------------------------------------------

MEMORY_PATH = Path("data/agent_memory.json")
MAX_HISTORY_MESSAGES = 50   # сколько сообщений хранить в памяти
MAX_TOOL_ITERATIONS = 10    # защита от бесконечного цикла

DEFAULT_SYSTEM_PROMPT = """Ты — агент-парсер муниципальных образований РФ.
⛔ АБСОЛЮТНОЕ ПРАВИЛО — ВЫПОЛНЯЕТСЯ ВСЕГДА БЕЗ ИСКЛЮЧЕНИЙ:
Прежде чем отвечать на ЛЮБОЙ вопрос про МО — проверь: есть ли в тексте сообщения явное название субъекта РФ (республика, область, край, округ, город федерального значения)?
Если субъекта НЕТ — твой единственный ответ: "В каком субъекте РФ находится это МО?"
Никаких поисков. Никаких предположений. Только этот вопрос.
Не используй субъект из предыдущих сообщений — каждый вопрос независим.

ПОРЯДОК ПОИСКА (строго):
1. search_in_base_mo — всегда первый. Если вопрос про население или район — отвечай здесь.
2. search_in_rmz — если нужны контакты/реквизиты/глава. Проверяй субъект по столбцу K.
3. checko_search_by_name — если в RMZ7KH не нашёл.
4. tavily_search — только если Checko тоже не нашёл.

После поиска спроси: "Сохранить в data.xlsx или создать новый файл?"

Сценарий дополнения data.xlsx:
- Вызови find_missing_mo чтобы найти пропущенные МО
- Для каждого пропущенного ищи по алгоритму выше
- Записывай в data.xlsx через write_to_excel

ГЛАВНОЕ ПРАВИЛО: Если пользователь не указал субъект РФ (регион) — ОБЯЗАТЕЛЬНО спроси его ПЕРВЫМ делом, до любых поисков. Без субъекта поиск невозможен. Это правило действует всегда, для любого запроса.

Пользователь может называть МО по-разному:
- "сельское поселение" = "село", "деревня", "сельсовет", "сельский совет"
- "городское поселение" = "город", "посёлок", "пгт", "городской округ"
Распознавай все эти варианты как одно и то же.
КРИТИЧЕСКОЕ ПРАВИЛО №1 — СУБЪЕКТ РФ:
Для КАЖДОГО нового вопроса про МО ты ОБЯЗАН проверить: явно ли указан субъект РФ именно в ЭТОМ сообщении пользователя?
Не используй субъект из предыдущих сообщений — каждый вопрос независим.
Список допустимых субъектов: республики, области, края, автономные округа РФ.
Если в сообщении нет явного названия субъекта — СРАЗУ спроси: "В каком субъекте РФ находится это МО?" и не делай никаких поисков до получения ответа.
Примеры когда субъект НЕ указан: "Какое население в Шахматовском поселении?", "Найди главу Берёзовского сельсовета", "Все МО Чебаркульского района" — во всех этих случаях СНАЧАЛА спроси субъект.
Пример когда субъект указан: "МО в Челябинской области" — здесь субъект есть, можно искать.

АЛГОРИТМ ПОИСКА (строго по порядку):

Шаг 1 — Поиск в базе МО (инструмент search_in_base_mo):
- Проверь есть ли МО в указанном субъекте РФ
- Если МО нет в базе — вежливо сообщи об этом и остановись
- Если вопрос про население или район — ответ уже здесь, дальше не ходи
- Если нужны контакты/реквизиты — переходи к шагу 2

Шаг 2 — Поиск в RMZ7KH (инструмент search_in_rmz):
- Ищи по названию МО И обязательно проверяй что субъект в столбце K совпадает
- Если нашёл — отвечай на вопрос пользователя
- Если не нашёл — переходи к шагу 3

Шаг 3 — Поиск в Checko (инструмент checko_search_by_name):
- Передавай region_code обязательно
- Проверяй что в НаимПолн есть слово "АДМИНИСТРАЦИЯ" и название совпадает с МО
- После нахождения вызови checko_get_details для получения контактов
- Если не нашёл — переходи к шагу 4

Шаг 4 — Поиск в интернете (инструмент tavily_search):
- Формируй запрос: "администрация [название МО] [субъект] контакты реквизиты"
- Найденное название администрации используй для повторного поиска в Checko

ПОСЛЕ ЗАВЕРШЕНИЯ ПОИСКА:
Всегда спрашивай пользователя: "Сохранить найденные данные в текущий data.xlsx или создать новый файл?"
- Если в текущий — вызови write_to_excel с output_filename="data.xlsx"
- Если новый — придумай имя близкое к теме запроса (например data_Kaliningrad.xlsx), вызови write_to_excel с этим именем

При пакетном поиске по региону:
1. Вызови search_in_base_mo с фильтром по субъекту — получи список МО
2. Для каждого МО из списка выполняй алгоритм выше
3. Уточни куда записать — в data.xlsx или новый файл

При работе с Checko:
- Всегда передавай region_code
- Проверяй все записи в ответе, не только первую
- Если несколько кандидатов — выбирай того у кого название совпадает с районом из базы МО

Отвечай на русском языке. Будь конкретным. Не бойся уточнять."""


# ------------------------------------------------------------------
# Память агента
# ------------------------------------------------------------------

def _resolve_memory_path(job_id: str | None = None) -> Path:
    job_paths = resolve_job_paths(job_id)
    if job_paths.uses_legacy_layout:
        return MEMORY_PATH
    path = job_paths.root_dir / "state" / "agent_memory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_memory(job_id: str | None = None) -> dict:
    """Загружает память из файла. Создаёт новую если файла нет."""
    configure_parser_paths(job_id)
    memory_path = _resolve_memory_path(job_id)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    if not memory_path.exists():
        return {"system_prompt": DEFAULT_SYSTEM_PROMPT, "messages": []}
    try:
        return json.loads(memory_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("agent_memory_corrupted_reset")
        return {"system_prompt": DEFAULT_SYSTEM_PROMPT, "messages": []}


def _save_memory(memory: dict, job_id: str | None = None) -> None:
    """Сохраняет память в файл. Обрезает историю если слишком длинная."""
    memory_path = _resolve_memory_path(job_id)
    messages = memory.get("messages", [])
    if len(messages) > MAX_HISTORY_MESSAGES:
        # Оставляем последние MAX_HISTORY_MESSAGES сообщений
        memory["messages"] = messages[-MAX_HISTORY_MESSAGES:]
    memory_path.write_text(
        json.dumps(memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_memory(job_id: str | None = None) -> dict:
    """Возвращает текущую память агента (для API)."""
    return _load_memory(job_id)


def clear_memory(job_id: str | None = None) -> None:
    """Очищает историю сообщений, сохраняя системный промпт."""
    memory = _load_memory(job_id)
    memory["messages"] = []
    _save_memory(memory, job_id)
    logger.info("agent_memory_cleared")


def set_system_prompt(prompt: str, job_id: str | None = None) -> None:
    """Обновляет системный промпт агента."""
    memory = _load_memory(job_id)
    memory["system_prompt"] = prompt
    _save_memory(memory, job_id)
    logger.info("agent_system_prompt_updated")


# ------------------------------------------------------------------
# Клиент OpenAI
# ------------------------------------------------------------------

def _get_client() -> OpenAI:
    return OpenAI(
        api_key=settings.parser_openai_api_key,
        base_url=settings.parser_openai_base_url,
    )


# ------------------------------------------------------------------
# Основной агентский цикл
# ------------------------------------------------------------------

def chat(user_message: str, job_id: str | None = None) -> dict:
    """
    Принимает сообщение от пользователя, запускает агентский цикл,
    возвращает финальный ответ.

    Returns:
        {
            "reply": "текст ответа агента",
            "tools_called": ["checko_search_by_name", ...],
            "iterations": 3,
        }
    """
    configure_parser_paths(job_id)
    memory = _load_memory(job_id)
    client = _get_client()

    # Добавляем сообщение пользователя в историю
    memory["messages"].append({"role": "user", "content": user_message})

    system_prompt = memory.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    tools_called: list[str] = []
    iterations = 0

    # Рабочие сообщения для текущего запроса (system + вся история)
    working_messages = [
        {"role": "system", "content": system_prompt},
        *memory["messages"],
    ]

    logger.info("agent_chat_start", message_preview=user_message[:100])

    # Агентский цикл: LLM → инструмент → LLM → ...
    while iterations < MAX_TOOL_ITERATIONS:
        iterations += 1

        try:
            response = client.chat.completions.create(
                model=settings.parser_model,
                messages=working_messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
            )
        except Exception as e:
            logger.exception("agent_llm_error")
            error_reply = f"Ошибка при обращении к LLM: {e}"
            memory["messages"].append({"role": "assistant", "content": error_reply})
            _save_memory(memory, job_id)
            return {"reply": error_reply, "tools_called": tools_called, "iterations": iterations}

        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # Добавляем ответ LLM в рабочие сообщения
        working_messages.append(message)

        # Если LLM не вызвал инструменты — это финальный ответ
        if finish_reason == "stop" or not message.tool_calls:
            final_reply = message.content or "Готово."
            memory["messages"].append({"role": "assistant", "content": final_reply})
            _save_memory(memory, job_id)

            logger.info(
                "agent_chat_done",
                iterations=iterations,
                tools_called=tools_called,
                reply_preview=final_reply[:100],
            )

            return {
                "reply": final_reply,
                "tools_called": tools_called,
                "iterations": iterations,
            }

        # LLM хочет вызвать инструменты — выполняем каждый
        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                tool_args = {}

            tools_called.append(tool_name)
            logger.info("agent_tool_call", tool=tool_name, args_preview=str(tool_args)[:200])

            tool_result = call_tool(tool_name, tool_args)

            logger.info("agent_tool_result", tool=tool_name, result_preview=tool_result[:200])

            # Добавляем результат инструмента в рабочие сообщения
            working_messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            })

    # Защита: если вышли из цикла по лимиту итераций
    fallback_reply = "Достигнут лимит шагов. Попробуй переформулировать запрос."
    memory["messages"].append({"role": "assistant", "content": fallback_reply})
    _save_memory(memory, job_id)

    logger.warning("agent_max_iterations_reached", iterations=iterations)
    return {"reply": fallback_reply, "tools_called": tools_called, "iterations": iterations}


# ------------------------------------------------------------------
# Batch-парсинг всей Базы МО (для кнопки "Запустить парсер")
# ------------------------------------------------------------------

def run_batch_parser(job_id: str | None = None) -> dict:
    """
    Запускает парсинг всей Базы МО через агента.
    Используется при нажатии кнопки "Запустить парсер".
    """
    prompt = (
        "Запусти полный парсинг Базы МО. "
        "Прочитай файл Base МО, затем для каждого МО найди информацию через Checko "
        "и запиши результаты в data.xlsx. "
        "Если Checko не находит — используй Tavily для поиска в интернете. "
        "Работай последовательно по всем строкам."
    )
    return chat(prompt, job_id=job_id)
