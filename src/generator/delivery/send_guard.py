from __future__ import annotations

import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import SendGuardState
from src.utils.config import settings


class SendGuardPaused(RuntimeError):
    pass


_fallback_counters: dict[str, list[float]] = {"sent": [], "complaints": [], "api_errors": [], "api_requests": []}
_fallback_lock = Lock()
_redis_client = None
_redis_checked = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _window_seconds() -> int:
    return max(300, int(settings.send_guard_window_seconds or 3600))


def _increment_counter(name: str, *, amount: int = 1) -> None:
    window_seconds = _window_seconds()
    now = time.time()
    redis = _get_redis()
    key = f"send_guard:{name}"
    if redis is not None:
        try:
            pipe = redis.pipeline()
            pipe.zadd(key, {f"{now}:{time.time_ns()}": now})
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.expire(key, window_seconds + 120)
            pipe.execute()
            return
        except Exception:
            pass
    cutoff = now - window_seconds
    with _fallback_lock:
        entries = [item for item in _fallback_counters.get(name, []) if item >= cutoff]
        entries.extend([now] * max(1, int(amount)))
        _fallback_counters[name] = entries


def _counter_value(name: str) -> int:
    window_seconds = _window_seconds()
    now = time.time()
    redis = _get_redis()
    key = f"send_guard:{name}"
    if redis is not None:
        try:
            redis.zremrangebyscore(key, 0, now - window_seconds)
            return int(redis.zcard(key) or 0)
        except Exception:
            pass
    cutoff = now - window_seconds
    with _fallback_lock:
        return len([item for item in _fallback_counters.get(name, []) if item >= cutoff])


def _load_guard_state() -> SendGuardState:
    with session_scope() as session:
        row = session.get(SendGuardState, 1)
        if row is None:
            row = SendGuardState(id=1, paused=False, updated_at=_now())
            session.add(row)
            session.flush()
        return row


def is_sending_paused() -> bool:
    with session_scope() as session:
        row = session.get(SendGuardState, 1)
        return bool(row and row.paused)


def get_send_guard_status() -> dict[str, Any]:
    window_seconds = _window_seconds()
    sent = _counter_value("sent")
    complaints = _counter_value("complaints")
    api_errors = _counter_value("api_errors")
    api_requests = _counter_value("api_requests")
    complaint_rate = (complaints / sent) if sent else 0.0
    api_error_rate = (api_errors / api_requests) if api_requests else 0.0
    with session_scope() as session:
        row = session.get(SendGuardState, 1)
        paused = bool(row and row.paused)
        pause_reason = str(row.pause_reason or "") if row else ""
        paused_at = row.paused_at.isoformat() if row and row.paused_at else ""
    return {
        "paused": paused,
        "pause_reason": pause_reason,
        "paused_at": paused_at,
        "window_seconds": window_seconds,
        "sent": sent,
        "complaints": complaints,
        "complaint_rate": complaint_rate,
        "complaint_rate_threshold": float(settings.send_guard_complaint_rate_threshold or 0.001),
        "api_errors": api_errors,
        "api_requests": api_requests,
        "api_error_rate": api_error_rate,
        "api_error_rate_threshold": float(settings.send_guard_api_error_rate_threshold or 0.05),
        "min_samples": max(1, int(settings.send_guard_min_samples or 20)),
    }


def pause_sending(reason: str) -> None:
    now = _now()
    with session_scope() as session:
        row = session.get(SendGuardState, 1)
        if row is None:
            row = SendGuardState(id=1, paused=True, pause_reason=str(reason or ""), paused_at=now, updated_at=now)
            session.add(row)
            return
        row.paused = True
        row.pause_reason = str(reason or "")
        row.paused_at = now
        row.updated_at = now


def resume_sending() -> None:
    now = _now()
    with session_scope() as session:
        row = session.get(SendGuardState, 1)
        if row is None:
            row = SendGuardState(id=1, paused=False, updated_at=now)
            session.add(row)
            return
        row.paused = False
        row.pause_reason = None
        row.paused_at = None
        row.updated_at = now


def assert_sending_allowed() -> None:
    if is_sending_paused():
        status = get_send_guard_status()
        reason = status.get("pause_reason") or "Отправка временно приостановлена."
        raise SendGuardPaused(str(reason))


def record_sent(*, count: int = 1) -> None:
    _increment_counter("sent", amount=count)
    evaluate_thresholds()


def record_complaint(*, count: int = 1) -> None:
    _increment_counter("complaints", amount=count)
    evaluate_thresholds()


def record_api_request(*, success: bool) -> None:
    _increment_counter("api_requests")
    if not success:
        _increment_counter("api_errors")
    evaluate_thresholds()


def evaluate_thresholds() -> None:
    """Intentionally a no-op: the blanket, account-wide auto-pause this used
    to trigger was a design mistake — a single connection/key's own
    channel_guard.py already isolates and self-heals (via its "warmup"
    action) on that connection's own error spikes, so halting sending for
    every sender in the system over one connection's blip was both
    redundant and too aggressive (5% of just 20 API calls — a single
    transient failure — was enough to trip it). Kept as a no-op, rather than
    deleted outright, so record_sent/record_complaint/record_api_request and
    get_send_guard_status() keep working unchanged: the rolling
    sent/complaint/api-error counters are still tracked and visible via
    /api/sender/status, they just no longer pause anything.
    pause_sending()/resume_sending()/is_sending_paused() are untouched, so a
    manual pause (if one is ever wired up again) still works.
    """
    return
