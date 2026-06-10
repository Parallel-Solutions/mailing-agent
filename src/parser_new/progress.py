"""
src/parser_new/progress.py — канал прогресса парсера для вывода хода работы в чат.

Зачем нужен:
  Сбор данных идёт внутри одного блокирующего вызова (batch_processor), и обычный
  ответ POST /api/parser/chat приходит только в самом конце. Чтобы пользователь
  видел процесс по ходу дела, мы шлём короткие сообщения в сторону — в Redis,
  а SSE-эндпоинт пересылает их браузеру.

Как устроено:
  - job_id текущего запроса кладётся в contextvars (его НЕЛЬЗЯ протащить через
    аргументы инструментов LLM — модель сама заполняет параметры из текста,
    поэтому используем контекст выполнения, а не аргумент; глобалка сломала бы
    многопоточность, contextvars изолирован по задаче/потоку).
  - emit() из любой точки сбора (oktmo_tool, batch_processor) пишет короткое
    сообщение в Redis-список по job_id.
  - subscribe() в SSE-эндпоинте читает этот список по курсору и отдаёт сообщения
    браузеру; завершается по сигналу _DONE или по таймауту простоя.

Деградация:
  Если Redis недоступен — emit() и subscribe() тихо ничего не делают. Сбор данных
  при этом идёт как обычно, а финальный ответ POST приходит штатно. Прогресс —
  необязательная надстройка, он не должен ломать основную работу.
"""
from __future__ import annotations

import os
import json
import time
import contextvars
from typing import Iterator, Optional

from src.parser_new.logger import logger

# Переиспользуем то же подключение к Redis, что и кэш — отдельный коннект не плодим.
from src.parser_new.memory.cache_memory import _get_redis


# job_id текущего запроса. default=None -> вне запроса (например, запуск из консоли)
_current_job_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "parser_progress_job_id", default=None
)

_CHANNEL_PREFIX = "parser:progress:"
_DONE = "__DONE__"               # спец-сигнал завершения потока
_STREAM_TTL = 60 * 60            # сколько Redis хранит список сообщений (сек)
_IDLE_TIMEOUT = 300.0            # закрыть SSE, если новых сообщений нет столько сек
_POLL = 0.4                      # как часто SSE-эндпоинт опрашивает список (сек)


def _channel(job_id: str) -> str:
    return f"{_CHANNEL_PREFIX}{job_id}"


# ==============================
# СТОРОНА ЗАПИСИ (вызывается из chat / инструментов сбора)
# ==============================

_ENV_KEY = "PARSER_PROGRESS_JOB_ID"


def set_job(job_id: Optional[str]) -> None:
    """Запоминает job_id текущего запроса — и в contextvar, и в окружении процесса.
    Окружение видно из любого потока, поэтому переживает переход
    chat() → агент LangGraph → инструмент (где contextvar теряется)."""
    _current_job_id.set(job_id)
    if job_id:
        os.environ[_ENV_KEY] = job_id
    else:
        os.environ.pop(_ENV_KEY, None)


def get_job() -> Optional[str]:
    return _current_job_id.get() or os.environ.get(_ENV_KEY) or None


def start(job_id: Optional[str]) -> None:
    """
    Начало запроса: фиксирует job_id и очищает хвост прошлого прогона.
    Вызывать в chat() ДО агента.
    """
    set_job(job_id)
    if not job_id:
        return
    try:
        r = _get_redis()
        if r:
            r.delete(_channel(job_id))
    except Exception as e:
        logger.debug(f"[progress] start failed: {e}")


def emit(text: str, kind: str = "progress") -> None:
    """
    Шлёт короткое сообщение прогресса в канал текущего job_id.
    Молча выходит, если мы вне запроса или Redis недоступен.
    """
    job_id = get_job()
    if not job_id:
        return
    try:
        r = _get_redis()
        if not r:
            return
        payload = json.dumps(
            {"kind": kind, "text": text, "ts": time.time()},
            ensure_ascii=False,
        )
        ch = _channel(job_id)
        r.rpush(ch, payload)
        r.expire(ch, _STREAM_TTL)
        logger.debug(f"[progress] {job_id}: {text}")
    except Exception as e:
        logger.debug(f"[progress] emit failed: {e}")


def finish() -> None:
    """Помечает поток завершённым и сбрасывает job_id процесса."""
    emit(_DONE, kind="done")
    set_job(None)


# ==============================
# СТОРОНА ЧТЕНИЯ (SSE-эндпоинт в main.py)
# ==============================

def _sse(data: dict) -> str:
    """Формат одного события SSE: 'data: {...}\\n\\n'."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def subscribe(job_id: str) -> Iterator[str]:
    """
    Генератор SSE-строк для одного job_id.

    Отдаёт уже накопленные и новые сообщения по мере появления. Завершается
    при сигнале _DONE или после _IDLE_TIMEOUT секунд тишины. Это обычный
    sync-генератор — Starlette сам прогоняет его в пуле потоков, поэтому
    time.sleep здесь не блокирует событийный цикл.
    """
    ch = _channel(job_id)
    cursor = 0
    last_activity = time.time()

    # стартовое событие, чтобы фронт сразу понял, что соединение живо
    yield _sse({"kind": "open"})

    while True:
        items = []
        try:
            r = _get_redis()
            if r:
                items = r.lrange(ch, cursor, -1) or []
        except Exception as e:
            logger.debug(f"[progress] subscribe read failed: {e}")
            items = []

        for raw in items:
            cursor += 1
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if data.get("text") == _DONE:
                yield _sse({"kind": "done"})
                return
            yield _sse(data)
            last_activity = time.time()

        if time.time() - last_activity > _IDLE_TIMEOUT:
            yield _sse({"kind": "timeout"})
            return

        time.sleep(_POLL)