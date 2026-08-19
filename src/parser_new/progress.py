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
# Раньше SSE закрывался после 5 мин тишины — и делал это РАНЬШЕ, чем сдавался фронт
# (10 мин), лишая фронт единственного способа продлить ожидание. Теперь поток
# закрывается штатно по сигналу _DONE (он всегда приходит из finish() в finally),
# а в тишине шлём keep-alive «пульс», чтобы и поток, и таймер клиента жили.
# _IDLE_TIMEOUT остаётся лишь СТРАХОВКОЙ на случай жёсткого убийства процесса,
# когда _DONE не пришёл, — поэтому он большой.
_IDLE_TIMEOUT = 30 * 60          # закрыть SSE только если РЕАЛЬНЫХ сообщений нет столько сек
_HEARTBEAT = 15.0                # слать «пульс», если в тишине нет событий столько сек
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
            r.delete(f"parser:stop:{job_id}")   # снять флаг остановки прошлого прогона
    except Exception as e:
        logger.debug(f"[progress] start failed: {e}")

def request_stop(job_id: str | None) -> None:
    """Ставит флаг остановки для job_id (живёт 1 час)."""
    if not job_id:
        return
    try:
        r = _get_redis()
        if r:
            r.setex(f"parser:stop:{job_id}", 3600, "1")
    except Exception as e:
        logger.debug(f"[progress] request_stop failed: {e}")


def is_stop_requested(job_id: str | None = None) -> bool:
    """Проверяет флаг остановки. job_id берём из контекста, если не передан."""
    job_id = job_id or get_job()
    if not job_id:
        return False
    try:
        r = _get_redis()
        return bool(r and r.exists(f"parser:stop:{job_id}"))
    except Exception:
        return False


def clear_stop(job_id: str | None) -> None:
    """Снимает флаг — вызывать в start(), чтобы прошлый стоп не убил новый прогон."""
    if not job_id:
        return
    try:
        r = _get_redis()
        if r:
            r.delete(f"parser:stop:{job_id}")
    except Exception:
        pass


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
    now0 = time.time()
    last_message = now0     # когда пришло последнее РЕАЛЬНОЕ сообщение (для страховки)
    last_sent = now0        # когда клиенту ушло последнее событие (сообщение или пульс)

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
            now = time.time()
            last_message = now
            last_sent = now

        now = time.time()
        # keep-alive: не молчим в сторону клиента дольше _HEARTBEAT — так и SSE-поток,
        # и таймер тишины на фронте остаются живыми, пока сервер занят сбором.
        if now - last_sent >= _HEARTBEAT:
            yield _sse({"kind": "ping", "ts": now})
            last_sent = now

        # Страховка: РЕАЛЬНЫХ сообщений нет очень долго — вероятно, задача умерла,
        # не прислав _DONE (жёсткий kill). Тогда закрываемся, чтобы не течь вечно.
        if now - last_message > _IDLE_TIMEOUT:
            yield _sse({"kind": "timeout"})
            return

        time.sleep(_POLL)