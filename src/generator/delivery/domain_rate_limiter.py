from __future__ import annotations

import json
import time
from threading import Lock
from typing import Any

from src.generator.delivery.manager_stats import EMAIL_DOMAIN_PROVIDERS
from src.utils.config import settings


_fallback_counters: dict[str, list[float]] = {}
_fallback_lock = Lock()
_redis_client = None
_redis_checked = False


def _get_redis():
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    try:
        import redis

        client = redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _redis_client = client
    except Exception:
        _redis_client = None
    return _redis_client


def _parse_limits() -> dict[str, int]:
    raw = str(settings.sender_domain_limits_json or "").strip()
    if not raw:
        return {"other": 30}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"other": 30}
    if not isinstance(parsed, dict):
        return {"other": 30}
    limits: dict[str, int] = {}
    for key, value in parsed.items():
        try:
            limits[str(key).strip().lower()] = max(1, int(value))
        except (TypeError, ValueError):
            continue
    if "other" not in limits:
        limits["other"] = 30
    return limits


def recipient_domain_bucket(email: str) -> str:
    normalized = str(email or "").strip().lower()
    domain = normalized.split("@")[-1] if "@" in normalized else ""
    if not domain:
        return "other"
    if domain in EMAIL_DOMAIN_PROVIDERS:
        provider = EMAIL_DOMAIN_PROVIDERS[domain]
        if provider == "Gmail":
            return "gmail.com"
        if provider == "Mail.ru":
            return "mail.ru"
        if provider == "Yandex":
            return "yandex.ru"
        if provider == "Outlook":
            return "outlook.com"
    limits = _parse_limits()
    if domain in limits:
        return domain
    return "other"


def _window_seconds() -> int:
    return max(60, int(settings.sender_domain_limit_window_seconds or 3600))


def _bucket_limit(bucket: str) -> int:
    limits = _parse_limits()
    return max(1, int(limits.get(bucket) or limits.get("other") or 30))


def _fallback_acquire(bucket: str, *, limit: int, window_seconds: int) -> tuple[bool, float]:
    now = time.time()
    cutoff = now - window_seconds
    with _fallback_lock:
        entries = [item for item in _fallback_counters.get(bucket, []) if item >= cutoff]
        if len(entries) >= limit:
            wait_seconds = max(0.0, entries[0] + window_seconds - now)
            _fallback_counters[bucket] = entries
            return False, wait_seconds
        entries.append(now)
        _fallback_counters[bucket] = entries
        return True, 0.0


def acquire_domain_slot(email: str) -> tuple[bool, float, str]:
    bucket = recipient_domain_bucket(email)
    limit = _bucket_limit(bucket)
    window_seconds = _window_seconds()
    redis = _get_redis()
    if redis is not None:
        key = f"domain_limit:{bucket}"
        try:
            pipe = redis.pipeline()
            now = time.time()
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.zcard(key)
            _, count = pipe.execute()
            if int(count or 0) >= limit:
                oldest = redis.zrange(key, 0, 0, withscores=True)
                if oldest:
                    wait_seconds = max(0.0, float(oldest[0][1]) + window_seconds - now)
                else:
                    wait_seconds = 1.0
                return False, wait_seconds, bucket
            member = f"{now}:{time.time_ns()}"
            redis.zadd(key, {member: now})
            redis.expire(key, window_seconds + 60)
            return True, 0.0, bucket
        except Exception:
            pass
    allowed, wait_seconds = _fallback_acquire(bucket, limit=limit, window_seconds=window_seconds)
    return allowed, wait_seconds, bucket


def wait_for_domain_slot(email: str, *, should_continue: Any | None = None) -> bool:
    while True:
        allowed, wait_seconds, _bucket = acquire_domain_slot(email)
        if allowed:
            return True
        if should_continue is not None and not bool(should_continue()):
            return False
        time.sleep(min(max(wait_seconds, 0.5), 30.0))


def get_domain_stats() -> dict[str, Any]:
    limits = _parse_limits()
    window_seconds = _window_seconds()
    redis = _get_redis()
    buckets: dict[str, Any] = {}
    for bucket, limit in limits.items():
        count = 0
        if redis is not None:
            try:
                key = f"domain_limit:{bucket}"
                now = time.time()
                redis.zremrangebyscore(key, 0, now - window_seconds)
                count = int(redis.zcard(key) or 0)
            except Exception:
                count = 0
        else:
            with _fallback_lock:
                cutoff = time.time() - window_seconds
                count = len([item for item in _fallback_counters.get(bucket, []) if item >= cutoff])
        buckets[bucket] = {
            "sent_in_window": count,
            "limit": limit,
            "remaining": max(0, limit - count),
        }
    return {
        "window_seconds": window_seconds,
        "buckets": buckets,
    }
