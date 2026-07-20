"""Dependency readiness probes for /ready (and reusable infra checks)."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from src.utils.config import settings


def check_database() -> None:
    from src.infra.db import check_db_connection

    check_db_connection()


def check_redis() -> None:
    import redis

    client = redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=3)
    try:
        if client.ping() is not True:
            raise RuntimeError("redis ping failed")
    finally:
        client.close()


def check_object_store() -> None:
    from src.infra.object_store import ensure_bucket

    ensure_bucket()


def _gotenberg_base_urls() -> tuple[str, ...]:
    raw = os.getenv("GOTENBERG_BASE_URLS") or os.getenv("GOTENBERG_BASE_URL") or "http://gotenberg:3000"
    return tuple(url.strip().rstrip("/") for url in raw.split(",") if url.strip())


def _gotenberg_health_is_up(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    status = str(payload.get("status") or "").strip().lower()
    if status and status not in {"up", "ok"}:
        return False
    details = payload.get("details")
    if isinstance(details, dict):
        libreoffice = details.get("libreoffice")
        if isinstance(libreoffice, dict):
            libreoffice_status = str(libreoffice.get("status") or "").strip().lower()
            if libreoffice_status and libreoffice_status not in {"up", "ok"}:
                return False
    return True


def check_gotenberg() -> None:
    timeout = float(os.getenv("GOTENBERG_HEALTH_TIMEOUT_SECONDS") or 10)
    urls = _gotenberg_base_urls()
    if not urls:
        raise RuntimeError("no GOTENBERG_BASE_URLS configured")
    errors: list[str] = []
    for base in urls:
        endpoint = f"{base}/health"
        try:
            with urlopen(endpoint, timeout=min(3.0, timeout)) as response:  # noqa: S310 - internal health URL
                body = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                payload = {}
            if _gotenberg_health_is_up(payload):
                return
            errors.append(f"{endpoint}: status not up")
        except (URLError, TimeoutError, OSError) as exc:
            errors.append(f"{endpoint}: {exc.__class__.__name__}")
    raise RuntimeError("; ".join(errors) or "gotenberg down")


def collect_readiness() -> dict[str, Any]:
    """Return readiness payload. status is ok only when every dependency is up."""
    probes = {
        "database": check_database,
        "redis": check_redis,
        "object_store": check_object_store,
        "gotenberg": check_gotenberg,
    }
    components: dict[str, str] = {}
    failures: dict[str, str] = {}
    for name, probe in probes.items():
        try:
            probe()
            components[name] = "up"
        except Exception as exc:  # noqa: BLE001 - surface generic readiness failure
            components[name] = "down"
            failures[name] = str(exc.__class__.__name__)
    payload: dict[str, Any] = {
        "status": "ok" if not failures else "error",
        **components,
    }
    if failures:
        payload["detail"] = failures
    return payload
