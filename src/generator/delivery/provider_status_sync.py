"""Pull delivery status/events from provider APIs into job_events streams.

RuSender/MailoPost historically relied on webhooks. This module adds an active
«добор» (same idea as UniSender Go event-dump): for each known provider message
id in ``sent_mail_log``, query the provider and ingest events via the existing
webhook parsers.

Note: RuSender's public OpenAPI does not expose a bulk send-history list. Sync
requires ``provider_message_id`` / task uuid already present in ``sent_mail_log``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

from src.generator.delivery.mailopost_events import append_mailopost_events
from src.generator.delivery.rusender_events import append_rusender_events
from src.jobs.job_docs import list_job_ids_with_sent_mail, read_sent_mail_log
from src.utils.config import settings
from src.utils.logger import logger


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _message_ids_from_item(item: dict[str, Any]) -> list[str]:
    from src.generator.delivery.provider_ids import provider_message_id_lookup_keys

    provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
    values = [
        item.get("provider_message_id"),
        item.get("message_id"),
        item.get("provider_job_id"),
        provider.get("message_id"),
        provider.get("uuid"),
        provider.get("task_id"),
    ]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        for text in provider_message_id_lookup_keys(value):
            if text and text not in seen:
                seen.add(text)
                result.append(text)
    return result


def _transport_of(item: dict[str, Any]) -> str:
    provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
    return (_safe_text(provider.get("provider")) or _safe_text(item.get("transport"))).lower()


def collect_provider_message_ids(job_id: str | None = None) -> dict[str, list[tuple[str, str]]]:
    """Return {transport: [(job_id, message_id), ...]} from sent_mail_log."""

    job_ids = [job_id] if job_id else list_job_ids_with_sent_mail()
    buckets: dict[str, list[tuple[str, str]]] = {"rusender": [], "mailopost": []}
    seen: set[tuple[str, str, str]] = set()
    for jid in job_ids:
        for item in read_sent_mail_log(jid):
            transport = _transport_of(item)
            if transport not in buckets:
                continue
            for message_id in _message_ids_from_item(item):
                key = (transport, jid or "", message_id)
                if key in seen:
                    continue
                seen.add(key)
                buckets[transport].append((jid or "", message_id))
    return buckets


def _fetch_rusender_message(task_id: str) -> dict[str, Any]:
    from src.generator.delivery.sender_agent import (
        _build_rusender_url,
        _run_rusender_request,
        _rusender_auth_headers,
    )

    api_key = _safe_text(settings.rusender_api_key)
    if not api_key:
        raise RuntimeError("Не указан RUSENDER_API_KEY.")
    request = Request(
        _build_rusender_url(f"external-mails/{task_id}"),
        method="GET",
        headers={
            "Accept": "application/json",
            **_rusender_auth_headers(api_key),
        },
    )
    raw = _run_rusender_request(request, timeout=30)
    data = json.loads(raw) if raw else {}
    return data if isinstance(data, dict) else {}


def _fetch_mailopost_message(message_id: str) -> dict[str, Any]:
    from src.generator.delivery.sender_agent import _run_mailopost_request

    token = _safe_text(settings.mailopost_api_token)
    base = _safe_text(settings.mailopost_api_base_url) or "https://api.mailopost.ru/v1"
    if not token:
        raise RuntimeError("Не указан MAILOPOST_API_TOKEN.")
    request = Request(
        f"{base.rstrip('/')}/email/messages/{message_id}",
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    raw = _run_mailopost_request(request, timeout=30)
    data = json.loads(raw) if raw else {}
    return data if isinstance(data, dict) else {}


def _rusender_payload_from_api(task_id: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize RuSender API payload into webhook-shaped events for append_rusender_events."""

    events = data.get("events")
    if isinstance(events, list) and events:
        normalized: list[dict[str, Any]] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            event = dict(item)
            if "taskId" not in event and "task_id" not in event:
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if not payload.get("taskId"):
                    event.setdefault("payload", {**payload, "taskId": task_id})
            normalized.append(event)
        return normalized

    status = _safe_text(data.get("status") or data.get("state") or data.get("provider_status"))
    email = _safe_text(data.get("email") or data.get("recipient") or data.get("to"))
    trigger = {
        "delivered": "external_mail.delivered",
        "opened": "external_mail.open",
        "open": "external_mail.open",
        "clicked": "external_mail.click",
        "click": "external_mail.click",
        "hard_bounced": "external_mail.hard_bounced",
        "soft_bounced": "external_mail.soft_bounced",
        "unsubscribed": "external_mail.unsubscribe",
        "spam": "external_mail.complaint",
        "complaint": "external_mail.complaint",
        "error": "external_mail.error",
        "failed": "external_mail.error",
    }.get(status.lower(), "")
    if not trigger:
        return []
    return [
        {
            "trigger": trigger,
            "payload": {
                "taskId": task_id,
                "email": email,
            },
            "createdAt": _safe_text(data.get("updated_at") or data.get("created_at"))
            or datetime.now().isoformat(timespec="seconds"),
        }
    ]


def _mailopost_payload_from_api(message_id: str, data: dict[str, Any]) -> dict[str, Any]:
    status = _safe_text(data.get("status") or data.get("state"))
    return {
        "id": message_id,
        "message_id": message_id,
        "status": status,
        "to": _safe_text(data.get("to") or data.get("recipient") or data.get("email")),
        "updated_at": _safe_text(data.get("updated_at") or data.get("sent_at")),
        "event": data,
    }


def sync_rusender_events_for_ids(task_ids: list[str]) -> dict[str, Any]:
    fetched = 0
    ingested = 0
    failed = 0
    unsupported = 0
    errors: list[str] = []
    for task_id in task_ids:
        try:
            data = _fetch_rusender_message(task_id)
            fetched += 1
            payload = _rusender_payload_from_api(task_id, data)
            if not payload:
                unsupported += 1
                continue
            result = append_rusender_events(payload)
            ingested += int(result.get("saved") or 0)
        except HTTPError as exc:
            failed += 1
            if exc.code == 404:
                unsupported += 1
                errors.append(f"rusender {task_id}: HTTP 404 (API history unavailable)")
            else:
                errors.append(f"rusender {task_id}: HTTP {exc.code}")
        except Exception as exc:
            failed += 1
            errors.append(f"rusender {task_id}: {exc}")
            logger.warning("rusender_status_sync_failed", task_id=task_id, error=str(exc))
    return {
        "provider": "rusender",
        "requested": len(task_ids),
        "fetched": fetched,
        "ingested": ingested,
        "failed": failed,
        "unsupported": unsupported,
        "errors": errors[:20],
    }


def sync_mailopost_events_for_ids(message_ids: list[str]) -> dict[str, Any]:
    fetched = 0
    ingested = 0
    failed = 0
    unsupported = 0
    errors: list[str] = []
    for message_id in message_ids:
        try:
            data = _fetch_mailopost_message(message_id)
            fetched += 1
            payload = _mailopost_payload_from_api(message_id, data)
            if not _safe_text(payload.get("status")):
                unsupported += 1
                continue
            result = append_mailopost_events(payload)
            ingested += int(result.get("saved") or 0) if isinstance(result, dict) else 0
        except HTTPError as exc:
            failed += 1
            errors.append(f"mailopost {message_id}: HTTP {exc.code}")
        except Exception as exc:
            failed += 1
            errors.append(f"mailopost {message_id}: {exc}")
            logger.warning("mailopost_status_sync_failed", message_id=message_id, error=str(exc))
    return {
        "provider": "mailopost",
        "requested": len(message_ids),
        "fetched": fetched,
        "ingested": ingested,
        "failed": failed,
        "unsupported": unsupported,
        "errors": errors[:20],
    }


def sync_provider_delivery_events(
    *,
    job_id: str | None = None,
    providers: tuple[str, ...] = ("rusender", "mailopost"),
) -> dict[str, Any]:
    buckets = collect_provider_message_ids(job_id)
    report: dict[str, Any] = {
        "job_id": job_id,
        "counts": {name: len(items) for name, items in buckets.items()},
        "providers": {},
    }
    if "rusender" in providers:
        ids = [message_id for _, message_id in buckets.get("rusender") or []]
        report["providers"]["rusender"] = sync_rusender_events_for_ids(ids)
    if "mailopost" in providers:
        ids = [message_id for _, message_id in buckets.get("mailopost") or []]
        report["providers"]["mailopost"] = sync_mailopost_events_for_ids(ids)
    return report


def refresh_provider_events_for_job(job_id: str | None, items: list[dict[str, Any]]) -> str:
    """Called from analytics refresh; returns human-readable note (may be empty)."""

    rusender_ids: list[str] = []
    mailopost_ids: list[str] = []
    for item in items:
        transport = _transport_of(item)
        ids = _message_ids_from_item(item)
        if transport == "rusender":
            rusender_ids.extend(ids)
        elif transport == "mailopost":
            mailopost_ids.extend(ids)

    notes: list[str] = []
    if rusender_ids:
        result = sync_rusender_events_for_ids(list(dict.fromkeys(rusender_ids)))
        if result["ingested"]:
            notes.append(f"Добрали из RuSender {result['ingested']} событий доставки.")
        elif result["requested"] and result["unsupported"] >= result["requested"]:
            notes.append(
                "RuSender API не отдаёт историю отправок по task_id (нужны webhook-и или бэкап JSONL)."
            )
        elif result["failed"]:
            notes.append(f"RuSender: не удалось обновить статусы ({result['failed']} ошибок).")
    if mailopost_ids:
        result = sync_mailopost_events_for_ids(list(dict.fromkeys(mailopost_ids)))
        if result["ingested"]:
            notes.append(f"Добрали из MailoPost {result['ingested']} событий доставки.")
        elif result["failed"]:
            notes.append(f"MailoPost: не удалось обновить статусы ({result['failed']} ошибок).")
    return " ".join(notes)
