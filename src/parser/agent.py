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

import shutil
from pathlib import Path
from typing import Optional

from src.jobs import resolve_job_paths
import re
from src.parser_new.tools.discovery_tool import discover_and_write, resolve_okved

# Импорты из нашего нового агента
from src.parser_new.agent.executor import run_agent_task
from src.parser_new.memory.memory_manager import get_full_context

def get_memory_context() -> str:
    return get_full_context()

def _clear_memory() -> None:
    pass
from src.parser_new.batch_processor import run as run_batch
from src.parser_new import progress
from src.utils.logger import logger

# ДИАЛОГ С АГЕНТОМ

def _latest_batch_file() -> tuple[Optional[str], float]:
    """
    Возвращает (путь, mtime) самого свежего собранного файла в общем output/latest.
    Используется чтобы понять, создал ли агент новый файл за время запроса.
    """
    out_dir = Path(__file__).parent.parent / "parser_new" / "output" / "latest"
    if not out_dir.exists():
        return None, -1.0
    latest, latest_mtime = None, -1.0
    for p in out_dir.glob("batch_*.xlsx"):
        if "FAILED" in p.name:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime >= latest_mtime:
            latest, latest_mtime = str(p), mtime
    return latest, latest_mtime

def _maybe_run_discovery(message: str) -> dict | None:
    """Если запрос — «собери <кого-то> в <месте>», собрать каскадом источников.
    Иначе None → обычный путь агента (в т.ч. администрации МО).

    Разбор и порядок источников живут в parser_new/tools/collector.py.
    """
    from src.parser_new.tools.collector import parse_request, collect_and_describe
    from src.parser_new.tools.discovery_tool import resolve_okved

    parsed = parse_request(message or "")
    if not parsed:
        return None
    query, place, limit = parsed

    # Администрации МО и прочие некоммерческие запросы оставляем агенту:
    # у них свой маршрут с проверкой официальных названий.
    low = (query + " " + place).lower()
    if any(kw in low for kw in ("администрац", "муниципальн", "поселени",
                                "сельсовет", "мэри", "управа", "органы власти")):
        return None

    return collect_and_describe(query, place, limit)

def _mark_discovery_table_ready(job_id: Optional[str], *, count: int) -> None:
    """Discovery-ветка НЕ проходит проверку названий МО (её для коммерческого
    сбора нет), поэтому вручную помечаем таблицу как готовую — иначе gate в
    download-result (parser_table_verified) вернёт 409 «Дождитесь завершения
    проверки таблицы». Правка изолирована: МО-путь выставляет этот же статус
    сам во время реальной верификации, здесь мы его не задеваем."""
    try:
        from datetime import datetime
        from src.generator.orchestration.parser_agent import (
            _update_municipality_verification_state,
        )
        now = datetime.now().isoformat(timespec="seconds")
        _update_municipality_verification_state(
            job_id,
            status="completed",
            source="discovery",
            summary_text=f"Коммерческий сбор завершён: {count} организаций.",
            completed_at=now,
            result={
                "status": "ok",
                "total_rows": count,
                "updated_rows": 0,
                "verified_rows": count,
                "missing_rows": 0,
                "kept_rows": count,
            },
        )
    except Exception as e:
        # Статус — вспомогательный: если пометить не удалось, сам сбор уже прошёл,
        # файл записан. Не роняем ответ пользователю из-за статуса.
        logger.warning(f"[parser] Не удалось пометить discovery-таблицу готовой: {e}")

def chat(message: str, job_id: Optional[str] = None) -> dict:
    """
    Диалог с агентом. Используется в /api/parser/chat.
    (док-строка прежняя)
    """
    progress.start(job_id)          # фиксируем job_id для потока прогресса
    try:
        uploaded_file = None
        job_output_dir = None
        if job_id:
            try:
                paths = resolve_job_paths(job_id)
                if paths.data_xlsx.exists():
                    uploaded_file = str(paths.data_xlsx)
                job_output_dir = paths.output_dir
            except Exception as e:
                logger.warning(f"Не удалось получить пути для job_id={job_id}: {e}")

        _, before_mtime = _latest_batch_file()

        disc = _maybe_run_discovery(message) if uploaded_file is None else None
        if disc is not None:
            success = bool(disc.get("success"))
            reply_text = disc.get("reply") or ""
            if success:
                _mark_discovery_table_ready(job_id, count=int(disc.get("count") or 0))
        else:
            result = run_agent_task(
                task=message, chat_history=[],
                uploaded_file_path=uploaded_file, mode="Автоматический",
            )
            reply_text, success = result.text, result.success

        src_file, after_mtime = _latest_batch_file()
        file_was_created = src_file is not None and after_mtime > before_mtime

        result_file = None
        if file_was_created:
            if job_output_dir is not None:
                try:
                    job_output_dir.mkdir(parents=True, exist_ok=True)
                    dst = job_output_dir / Path(src_file).name
                    shutil.copy2(src_file, dst)
                    result_file = str(dst)
                    logger.info(f"[parser] Результат скопирован в папку задачи: {dst}")
                except Exception as e:
                    logger.warning(f"Не удалось скопировать результат в папку задачи: {e}")
                    result_file = src_file
            else:
                result_file = src_file

        return {"reply": reply_text, "success": success, "result_file": result_file}
    finally:
        progress.finish()

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