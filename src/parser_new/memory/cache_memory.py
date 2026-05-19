"""
memory/cache_memory.py — быстрый кэш через Redis.

Хранит в оперативной памяти:
  - кэш ответов (чтобы не парсить одно и то же дважды)
  - счётчики rate limiting (не долбить сайты слишком часто)
  - состояние текущей сессии

Если Redis недоступен — падает в режим заглушки (dict в памяти).
Агент продолжит работу, просто без кэша.
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime
from typing import Any

from src.parser_new import config
from src.parser_new.logger import logger


# ==============================
# ПОДКЛЮЧЕНИЕ С FALLBACK
# ==============================

_redis_client = None
_fallback_cache: dict = {}   # заглушка если Redis недоступен
_use_fallback = False


def _get_redis():
    """Ленивое подключение к Redis."""
    global _redis_client, _use_fallback

    if _use_fallback:
        return None

    if _redis_client is None:
        try:
            import redis
            client = redis.from_url(config.REDIS_URL, decode_responses=True)
            client.ping()   # проверяем что Redis живой
            _redis_client = client
            logger.info("[cache] Redis подключён")
        except Exception as e:
            logger.warning(f"[cache] Redis недоступен, использую fallback: {e}")
            _use_fallback = True
            return None

    return _redis_client


def _cache_key(namespace: str, key: str) -> str:
    """Формирует ключ в формате 'agent:namespace:key'."""
    safe_key = hashlib.md5(key.encode()).hexdigest()[:16]
    return f"agent:{namespace}:{safe_key}"


# ==============================
# БАЗОВЫЕ ОПЕРАЦИИ
# ==============================

def cache_set(namespace: str, key: str, value: Any, ttl_seconds: int = 3600) -> None:
    """
    Сохраняет значение в кэш.

    Args:
        namespace:   группа ключей ('url_cache', 'session', 'ratelimit')
        key:         уникальный ключ внутри группы
        value:       любое сериализуемое значение
        ttl_seconds: время жизни в секундах (по умолчанию 1 час)
    """
    full_key     = _cache_key(namespace, key)
    serialized   = json.dumps(value, ensure_ascii=False, default=str)
    r = _get_redis()

    if r:
        r.setex(full_key, ttl_seconds, serialized)
    else:
        _fallback_cache[full_key] = {
            "value":   serialized,
            "expires": datetime.now().timestamp() + ttl_seconds,
        }


def cache_get(namespace: str, key: str) -> Any | None:
    """
    Читает значение из кэша.
    Возвращает None если ключа нет или он устарел.
    """
    full_key = _cache_key(namespace, key)
    r = _get_redis()

    if r:
        raw = r.get(full_key)
        return json.loads(raw) if raw else None
    else:
        entry = _fallback_cache.get(full_key)
        if not entry:
            return None
        if datetime.now().timestamp() > entry["expires"]:
            del _fallback_cache[full_key]
            return None
        return json.loads(entry["value"])


def cache_delete(namespace: str, key: str) -> None:
    full_key = _cache_key(namespace, key)
    r = _get_redis()
    if r:
        r.delete(full_key)
    else:
        _fallback_cache.pop(full_key, None)


# ==============================
# КЭШ URL — не парсить одно дважды
# ==============================

URL_CACHE_TTL = 60 * 60 * 4   # 4 часа


def get_cached_url(url: str) -> dict | None:
    """Возвращает закэшированный результат парсинга URL."""
    return cache_get("url_cache", url)


def set_cached_url(url: str, result: dict) -> None:
    """Кэширует результат парсинга URL на 4 часа."""
    cache_set("url_cache", url, result, ttl_seconds=URL_CACHE_TTL)
    logger.debug(f"[cache] URL закэширован: {url[:60]}")


# ==============================
# RATE LIMITING — не долбить сайты
# ==============================

def check_rate_limit(domain: str, max_per_minute: int = 10) -> bool:
    """
    Проверяет можно ли делать запрос к домену.

    Returns:
        True — можно, False — надо подождать
    """
    key      = f"rl:{domain}"
    full_key = _cache_key("ratelimit", key)
    r = _get_redis()

    if r:
        count = r.get(full_key)
        if count and int(count) >= max_per_minute:
            logger.warning(f"[cache] Rate limit для {domain}: {count}/{max_per_minute} в минуту")
            return False
        # Увеличиваем счётчик
        pipe = r.pipeline()
        pipe.incr(full_key)
        pipe.expire(full_key, 60)   # сбрасываем каждую минуту
        pipe.execute()
        return True
    else:
        # Без Redis — rate limiting не работает, просто разрешаем
        return True


# ==============================
# СЕССИЯ — что агент уже сделал
# ==============================

SESSION_TTL = 60 * 60 * 2   # 2 часа


def session_set(session_id: str, key: str, value: Any) -> None:
    """Сохраняет данные текущей сессии."""
    cache_set(f"session:{session_id}", key, value, ttl_seconds=SESSION_TTL)


def session_get(session_id: str, key: str) -> Any | None:
    """Читает данные текущей сессии."""
    return cache_get(f"session:{session_id}", key)


def session_add_processed(session_id: str, item: str) -> None:
    """Отмечает что этот URL/элемент уже обработан в сессии."""
    processed = session_get(session_id, "processed") or []
    if item not in processed:
        processed.append(item)
        session_set(session_id, "processed", processed)


def session_is_processed(session_id: str, item: str) -> bool:
    """Проверяет обрабатывался ли элемент в этой сессии."""
    processed = session_get(session_id, "processed") or []
    return item in processed


# ==============================
# КЭШ ОРГАНИЗАЦИЙ ПО ИНН
# В рамках одного запуска — не делать повторных запросов
# ==============================

_org_cache: dict = {}  # ИНН → данные организации


def org_cache_get(inn: str) -> dict | None:
    """Возвращает закэшированные данные организации по ИНН."""
    return _org_cache.get(inn.strip())


def org_cache_set(inn: str, data: dict) -> None:
    """Сохраняет данные организации в кэш сессии."""
    _org_cache[inn.strip()] = data
    logger.debug(f"[cache] Организация закэширована: ИНН {inn}")


def org_cache_clear() -> None:
    """Очищает кэш организаций (при старте новой сессии)."""
    _org_cache.clear()
