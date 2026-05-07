"""
Агент-парсер МО.

Принимает сообщения от пользователя, решает какие инструменты вызвать,
выполняет их и возвращает ответ. Хранит память в data/agent_memory.json.
"""

import json
from pathlib import Path

from openai import OpenAI

from src.parser.tools import TOOL_DEFINITIONS, call_tool
from src.utils.config import settings
from src.utils.logger import logger


# ------------------------------------------------------------------
# Константы
# ------------------------------------------------------------------

MEMORY_PATH = Path("data/agent_memory.json")
MAX_HISTORY_MESSAGES = 50   # сколько сообщений хранить в памяти
MAX_TOOL_ITERATIONS = 10    # защита от бесконечного цикла

DEFAULT_SYSTEM_PROMPT = """Ты — агент-парсер муниципальных образований РФ.

Твоя задача — находить информацию об администрациях муниципальных образований России:
ФИО главы, юридический адрес, email, телефон, ИНН, КПП, ОГРН и другие реквизиты.

У тебя есть инструменты:
- read_base_mo — читать файл с базой МО
- checko_search_by_name — искать организацию по названию в Checko
- checko_search_by_okved — искать все МО в регионе по ОКВЭД
- checko_get_details — получать полную карточку по ИНН
- tavily_search — искать в интернете если Checko не нашёл
- write_to_excel — записывать найденные данные в data.xlsx

Алгоритм поиска для каждого МО:
1. Сначала ищи через checko_search_by_name
2. Если не нашёл — ищи через tavily_search, уточни название администрации
3. С уточнённым названием снова пробуй checko_search_by_name
4. Когда нашёл организацию — обязательно вызови checko_get_details для получения контактов
5. Запиши результат через write_to_excel

При проверке результатов Checko убедись что:
- В названии организации есть слово "Администрация"
- Название соответствует искомому МО (район, тип поселения)

Отвечай на русском языке. Будь конкретным и информативным."""


# ------------------------------------------------------------------
# Память агента
# ------------------------------------------------------------------

def _load_memory() -> dict:
    """Загружает память из файла. Создаёт новую если файла нет."""
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MEMORY_PATH.exists():
        return {"system_prompt": DEFAULT_SYSTEM_PROMPT, "messages": []}
    try:
        return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("agent_memory_corrupted_reset")
        return {"system_prompt": DEFAULT_SYSTEM_PROMPT, "messages": []}


def _save_memory(memory: dict) -> None:
    """Сохраняет память в файл. Обрезает историю если слишком длинная."""
    messages = memory.get("messages", [])
    if len(messages) > MAX_HISTORY_MESSAGES:
        # Оставляем последние MAX_HISTORY_MESSAGES сообщений
        memory["messages"] = messages[-MAX_HISTORY_MESSAGES:]
    MEMORY_PATH.write_text(
        json.dumps(memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_memory() -> dict:
    """Возвращает текущую память агента (для API)."""
    return _load_memory()


def clear_memory() -> None:
    """Очищает историю сообщений, сохраняя системный промпт."""
    memory = _load_memory()
    memory["messages"] = []
    _save_memory(memory)
    logger.info("agent_memory_cleared")


def set_system_prompt(prompt: str) -> None:
    """Обновляет системный промпт агента."""
    memory = _load_memory()
    memory["system_prompt"] = prompt
    _save_memory(memory)
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

def chat(user_message: str) -> dict:
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
    memory = _load_memory()
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
            _save_memory(memory)
            return {"reply": error_reply, "tools_called": tools_called, "iterations": iterations}

        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # Добавляем ответ LLM в рабочие сообщения
        working_messages.append(message)

        # Если LLM не вызвал инструменты — это финальный ответ
        if finish_reason == "stop" or not message.tool_calls:
            final_reply = message.content or "Готово."
            memory["messages"].append({"role": "assistant", "content": final_reply})
            _save_memory(memory)

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
    _save_memory(memory)

    logger.warning("agent_max_iterations_reached", iterations=iterations)
    return {"reply": fallback_reply, "tools_called": tools_called, "iterations": iterations}


# ------------------------------------------------------------------
# Batch-парсинг всей Базы МО (для кнопки "Запустить парсер")
# ------------------------------------------------------------------

def run_batch_parser() -> dict:
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
    return chat(prompt)