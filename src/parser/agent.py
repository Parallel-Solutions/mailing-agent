"""
src/parser/agent.py

Прокси-слой между main.py (FastAPI) и нашим новым парсер-агентом.
Содержит функции, которые ожидает main.py:
  - chat(message, job_id)         — диалог с агентом
  - run_batch_parser(job_id)      — пакетная обработка файла
  - get_memory(job_id)             — текущая память агента
  - clear_memory(job_id)           — очистка памяти
  - set_system_prompt(prompt)      — кастомный системный промпт

Внутри использует:
  - src/parser_new/agent/         — для диалогового режима
  - src/parser_new/batch_processor — для пакетной обработки
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.jobs import resolve_job_paths

# Импорты из нашего нового агента
from src.parser_new.agent.executor import run_agent_task
from src.parser_new.memory.memory_manager import get_full_context

def get_memory_context() -> str:
    return get_full_context()

def _clear_memory() -> None:
    pass
from src.parser_new.batch_processor import run as run_batch
from src.utils.logger import logger


# ==============================
# ДИАЛОГ С АГЕНТОМ
# ==============================

def chat(message: str, job_id: Optional[str] = None) -> dict:
    """
    Диалог с агентом. Используется в /api/parser/chat.

    Args:
        message: текст вопроса
        job_id:  идентификатор задачи (опционально)

    Returns:
        {"reply": str, "success": bool}
    """
    # Если есть job_id — подтягиваем путь к данным
    uploaded_file = None
    if job_id:
        try:
            paths = resolve_job_paths(job_id)
            if paths.data_xlsx.exists():
                uploaded_file = str(paths.data_xlsx)
        except Exception as e:
            logger.warning(f"Не удалось получить путь файла для job_id={job_id}: {e}")

    result = run_agent_task(
        task=message,
        chat_history=[],
        uploaded_file_path=uploaded_file,
        mode="Автоматический",
    )
    return {"reply": result.text, "success": result.success}


# ==============================
# ПАКЕТНАЯ ОБРАБОТКА ФАЙЛА
# ==============================

def run_batch_parser(job_id: Optional[str] = None, limit: Optional[int] = None) -> dict:
    """
    Запускает пакетную обработку загруженного файла.
    Используется в /api/parser/start.

    Находит файл по job_id, запускает batch_processor,
    возвращает путь к итоговому файлу и статистику.

    Args:
        job_id: идентификатор задачи (берётся загруженный файл)
        limit:  опциональное ограничение количества строк

    Returns:
        {
            "status":      "ok" | "error",
            "reply":       краткое описание результата,
            "file":        путь к итоговому файлу,
            "failed_file": путь к файлу непроверенных,
            "task_stats":  { "found": N, "not_found": M, "liquidated": K },
        }
    """
    if not job_id:
        return {
            "status": "error",
            "reply": "Не указан job_id — невозможно определить файл для обработки.",
        }

    # Находим загруженный файл
    try:
        paths = resolve_job_paths(job_id)
    except Exception as e:
        logger.error(f"resolve_job_paths failed for {job_id}: {e}")
        return {"status": "error", "reply": f"Ошибка определения путей: {e}"}

    if not paths.data_xlsx.exists():
        return {
            "status": "error",
            "reply": "Файл data.xlsx не найден. Загрузите файл через интерфейс.",
        }

    # Запускаем пакетный обработчик
    try:
        logger.info(f"[parser] Запуск batch_processor для {paths.data_xlsx}")
        result = run_batch(
            file_path=str(paths.data_xlsx),
            save_every=10,
            output_dir=str(paths.output_dir),
        )

        return {
            "status": "ok",
            "reply": (
                f"Обработано: {result.get('processed', 0)}. "
                f"Найдено: {result.get('found', 0)}. "
                f"Не найдено: {result.get('not_found', 0)}. "
                f"Ликвидировано: {result.get('liquidated', 0)}."
            ),
            "file": result.get("output_path", ""),
            "failed_file": result.get("failed_path", ""),
            "task_stats": {
                "found":      result.get("found", 0),
                "not_found":  result.get("not_found", 0),
                "liquidated": result.get("liquidated", 0),
                "processed":  result.get("processed", 0),
            },
        }

    except Exception as e:
        logger.exception(f"[parser] batch_processor failed: {e}")
        return {
            "status": "error",
            "reply": f"Ошибка при обработке: {e}",
        }


# ==============================
# ПАМЯТЬ АГЕНТА
# ==============================

def get_memory(job_id: Optional[str] = None) -> dict:
    """Возвращает текущую память агента."""
    try:
        context = get_memory_context()
        return {"memory": context}
    except Exception as e:
        logger.warning(f"get_memory failed: {e}")
        return {"memory": ""}


def clear_memory(job_id: Optional[str] = None) -> None:
    """Очищает память агента."""
    try:
        _clear_memory()
    except Exception as e:
        logger.warning(f"clear_memory failed: {e}")


# ==============================
# СИСТЕМНЫЙ ПРОМПТ
# ==============================

def set_system_prompt(prompt: str, job_id: Optional[str] = None) -> None:
    """Сохраняет кастомный системный промпт."""
    if not prompt:
        return

    prompt_path = (
        Path(__file__).parent.parent / "parser_new" / "agent" / "prompt.py"
    )

    if not prompt_path.exists():
        logger.warning(f"Файл промпта не найден: {prompt_path}")
        return

    # Сохраняем как валидный Python модуль
    prompt_escaped = prompt.replace('"""', '\\"\\"\\"')
    prompt_path.write_text(
        f'SYSTEM_PROMPT = """{prompt_escaped}"""\n',
        encoding="utf-8",
    )
    logger.info(f"Системный промпт обновлён: {prompt_path}")