"""External-service spend ledger — backs the admin "external spend" tab.

Every billed call to a paid external service (LLM, email provider, email
validation, lookup API) gets one row here plus a push onto a global Redis
channel so the SSE endpoint (src/web/statistics_router.py) can deliver it to
open admin browser tabs immediately.

Mirrors src/parser_new/progress.py's emit/subscribe split, but the channel is
a single fixed key instead of one per job_id — this is a global feed, not
scoped to any one job (many billed calls, e.g. ad-hoc AI chat or template
generation, have no job_id at all).

Never raises: recording a spend must not break the caller's actual send/LLM
call. Any failure (DB down, Redis down) is logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import func, select

from src.infra.db import session_scope
from src.infra.llm_pricing import LlmUsage, estimate_llm_cost_usd
from src.infra.models import ExternalServiceSpend
from src.utils.config import settings

logger = logging.getLogger(__name__)

_CHANNEL = "external_spend:live"
_LIST_MAX_LEN = 2000       # LTRIM cap — a global channel fills up faster than a per-job one.
_STREAM_TTL = 60 * 60      # seconds Redis keeps the list around even if nobody is subscribed.
_HEARTBEAT = 15.0          # send a "ping" if the channel has been quiet this long.
_POLL = 0.4                # how often the SSE generator polls Redis.

_redis_client = None
_redis_checked = False

_price_cache: dict[str, float] | None = None
_price_cache_raw: str | None = None


def _get_redis():
    """Lazy Redis connection, degrades to None (no live push, DB write still happens)."""
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


def _static_prices() -> dict[str, float]:
    """Parses settings.external_service_prices_usd_json, re-parsing only when it changes."""
    global _price_cache, _price_cache_raw
    raw = str(settings.external_service_prices_usd_json or "").strip()
    if _price_cache is not None and raw == _price_cache_raw:
        return _price_cache
    prices: dict[str, float] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    try:
                        prices[str(key)] = float(value)
                    except (TypeError, ValueError):
                        continue
        except json.JSONDecodeError:
            logger.warning("[spend_ledger] invalid external_service_prices_usd_json, ignoring")
    _price_cache = prices
    _price_cache_raw = raw
    return prices


def _static_price_usd(service: str, operation: str) -> float:
    return _static_prices().get(f"{service}_{operation}", 0.0)


def _publish(payload: dict[str, Any]) -> None:
    try:
        redis_client = _get_redis()
        if not redis_client:
            return
        redis_client.rpush(_CHANNEL, json.dumps(payload, ensure_ascii=False))
        redis_client.ltrim(_CHANNEL, -_LIST_MAX_LEN, -1)
        redis_client.expire(_CHANNEL, _STREAM_TTL)
    except Exception as exc:
        logger.debug(f"[spend_ledger] publish failed: {exc}")


def _record(
    *,
    service: str,
    operation: str,
    model: str | None,
    request_count: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    cost_usd: float,
    job_id: str | None,
    owner_username: str | None,
    status: str,
    metadata: dict[str, Any] | None,
) -> None:
    row = {
        "service": service,
        "operation": operation,
        "model": model,
        "request_count": max(1, int(request_count or 1)),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": round(max(0.0, float(cost_usd or 0.0)), 6),
        "job_id": job_id or None,
        "owner_username": owner_username or None,
        "status": status or "ok",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        with session_scope() as db_session:
            db_session.add(ExternalServiceSpend(request_metadata=metadata or {}, **row))
    except Exception as exc:
        logger.debug(f"[spend_ledger] write failed: {exc}")
        return
    _publish({**row, "request_metadata": metadata or {}, "kind": "spend"})


def record_llm_usage(
    *,
    service: str,
    model: str,
    operation: str,
    usage: LlmUsage,
    job_id: str | None = None,
    owner_username: str | None = None,
    status: str = "ok",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log one LLM call, costed from actual token usage."""
    try:
        cost = estimate_llm_cost_usd(
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            image_count=usage.image_count,
        )
        _record(
            service=service,
            operation=operation,
            model=model,
            request_count=1,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=cost,
            job_id=job_id,
            owner_username=owner_username,
            status=status,
            metadata=metadata,
        )
    except Exception as exc:
        logger.debug(f"[spend_ledger] record_llm_usage failed: {exc}")


def record_service_call(
    *,
    service: str,
    operation: str,
    cost_usd: float | None = None,
    request_count: int = 1,
    job_id: str | None = None,
    owner_username: str | None = None,
    status: str = "ok",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log one call to a service with no cost in its own API response.

    cost_usd=None looks the price up from settings.external_service_prices_usd_json
    using the "{service}_{operation}" key; unknown keys default to 0.0.
    """
    try:
        resolved_cost = _static_price_usd(service, operation) if cost_usd is None else float(cost_usd)
        _record(
            service=service,
            operation=operation,
            model=None,
            request_count=request_count,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            cost_usd=resolved_cost * max(1, int(request_count or 1)),
            job_id=job_id,
            owner_username=owner_username,
            status=status,
            metadata=metadata,
        )
    except Exception as exc:
        logger.debug(f"[spend_ledger] record_service_call failed: {exc}")


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def subscribe() -> Iterator[str]:
    """SSE generator for the global spend channel — never terminates on its own;
    the client (or Starlette on disconnect) is what ends the stream."""
    cursor = 0
    last_sent = time.time()

    yield _sse({"kind": "open"})

    while True:
        items: list[str] = []
        try:
            redis_client = _get_redis()
            if redis_client:
                items = redis_client.lrange(_CHANNEL, cursor, -1) or []
        except Exception as exc:
            logger.debug(f"[spend_ledger] subscribe read failed: {exc}")
            items = []

        for raw in items:
            cursor += 1
            try:
                data = json.loads(raw)
            except Exception:
                continue
            yield _sse(data)
            last_sent = time.time()

        now = time.time()
        if now - last_sent >= _HEARTBEAT:
            yield _sse({"kind": "ping", "ts": now})
            last_sent = now

        time.sleep(_POLL)


def build_spend_snapshot(period_minutes: int = 1440, *, recent_limit: int = 100) -> dict[str, Any]:
    """Aggregates for the REST snapshot endpoint: totals, by-service breakdown, recent calls."""
    period_minutes = max(1, int(period_minutes or 1440))
    with session_scope() as db_session:
        # Compute the cutoff timestamp in Python (portable, no DB-specific
        # interval-cast SQL needed) and filter on the plain indexed column.
        cutoff_at = datetime.now(timezone.utc).timestamp() - period_minutes * 60
        cutoff_dt = datetime.fromtimestamp(cutoff_at, tz=timezone.utc)

        totals_row = db_session.execute(
            select(
                func.coalesce(func.sum(ExternalServiceSpend.cost_usd), 0),
                func.coalesce(func.sum(ExternalServiceSpend.request_count), 0),
            ).where(ExternalServiceSpend.created_at >= cutoff_dt)
        ).one()
        total_cost_usd = float(totals_row[0] or 0)
        total_requests = int(totals_row[1] or 0)

        by_service_rows = db_session.execute(
            select(
                ExternalServiceSpend.service,
                func.coalesce(func.sum(ExternalServiceSpend.cost_usd), 0),
                func.coalesce(func.sum(ExternalServiceSpend.request_count), 0),
            )
            .where(ExternalServiceSpend.created_at >= cutoff_dt)
            .group_by(ExternalServiceSpend.service)
            .order_by(func.sum(ExternalServiceSpend.cost_usd).desc())
        ).all()
        by_service = [
            {"service": row[0], "cost_usd": float(row[1] or 0), "request_count": int(row[2] or 0)}
            for row in by_service_rows
        ]

        by_bucket_rows = db_session.execute(
            select(
                func.date_trunc("hour", ExternalServiceSpend.created_at).label("bucket"),
                func.coalesce(func.sum(ExternalServiceSpend.cost_usd), 0),
            )
            .where(ExternalServiceSpend.created_at >= cutoff_dt)
            .group_by("bucket")
            .order_by("bucket")
        ).all()
        by_hour = [
            {"bucket": row[0].isoformat(timespec="minutes"), "cost_usd": float(row[1] or 0)}
            for row in by_bucket_rows
        ]

        recent_rows = db_session.execute(
            select(ExternalServiceSpend)
            .where(ExternalServiceSpend.created_at >= cutoff_dt)
            .order_by(ExternalServiceSpend.created_at.desc())
            .limit(max(1, int(recent_limit or 100)))
        ).scalars().all()
        recent_calls = [
            {
                "id": row.id,
                "service": row.service,
                "operation": row.operation,
                "model": row.model,
                "request_count": row.request_count,
                "cost_usd": float(row.cost_usd or 0),
                "job_id": row.job_id,
                "owner_username": row.owner_username,
                "status": row.status,
                "created_at": row.created_at.isoformat(timespec="seconds"),
            }
            for row in recent_rows
        ]

    return {
        "period_minutes": period_minutes,
        "total_cost_usd": total_cost_usd,
        "total_requests": total_requests,
        "by_service": by_service,
        "by_hour": by_hour,
        "recent_calls": recent_calls,
    }
