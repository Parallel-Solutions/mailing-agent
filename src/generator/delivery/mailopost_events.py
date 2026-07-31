from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from src.jobs import resolve_job_paths
from src.jobs.json_store import append_jsonl, path_lock, read_jsonl
from src.jobs.storage import JOBS_DIR
from src.utils.logger import logger


EVENTS_FILENAME = "mailopost_events.jsonl"
UNMATCHED_EVENTS_FILENAME = "mailopost_events_unmatched.jsonl"

EVENT_STATUS_MAP = {
    "delivered": "delivered",
    "hard_bounced": "hard_bounced",
    "hard_bounce": "hard_bounced",
    "bounced": "hard_bounced",
    "bounce": "hard_bounced",
    "soft_bounced": "soft_bounced",
    "soft_bounce": "soft_bounced",
    "failed": "err_delivery_failed",
    "failure": "err_delivery_failed",
    "error": "err_delivery_failed",
    "delivery_failed": "err_delivery_failed",
    "not_delivered": "err_delivery_failed",
    "undelivered": "err_delivery_failed",
    "rejected": "err_delivery_failed",
    "skipped": "skipped",
    "opened": "opened",
    "open": "opened",
    "clicked": "clicked",
    "click": "clicked",
    "unsubscribed": "unsubscribed",
    "unsubscribe": "unsubscribed",
    "complained": "spam",
    "complaint": "spam",
    "spam": "spam",
    "queued": "queued",
    "sent": "sent",
}


def append_mailopost_events(payload: Any) -> dict[str, Any]:
    events = _extract_events(payload)
    message_to_job = _load_message_job_index()
    saved = 0
    skipped = 0
    duplicates = 0
    unmatched = 0
    jobs: set[str] = set()
    existing_keys_by_path: dict[Path, set[str]] = {}

    for event in events:
        message_id = _extract_message_id(event)
        recipient = _extract_email(event)
        job_info = message_to_job.get(message_id, {}) if message_id else {}
        job_id = _safe_text(job_info.get("job_id"))
        event_type = _extract_event_type(event)
        record = {
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "event_id": _extract_event_id(event),
            "event_type": event_type,
            "provider_status": _status_from_event(event_type),
            "occurred_at": _extract_occurred_at(event),
            "message_id": message_id,
            "recipient": recipient or _safe_text(job_info.get("recipient")),
            "row_id": _safe_text(job_info.get("row_id")),
            "connection_id": _safe_text(job_info.get("connection_id")),
            "smtp_response": _extract_delivery_response(event),
            "event": event,
        }
        if not message_id and not record["recipient"] and not event_type:
            skipped += 1
            continue

        path = mailopost_events_path(job_id) if job_id else _unmatched_events_path()
        with path_lock(path):
            existing_keys = existing_keys_by_path.setdefault(path, _load_event_replay_keys(path))
            event_key = _event_replay_key(record)
            if event_key in existing_keys:
                duplicates += 1
                if job_id:
                    jobs.add(job_id)
                continue
            record["event_key"] = event_key
            append_jsonl(path, record)
            existing_keys.add(event_key)
        try:
            from src.campaigns.connection_sender_warmup_service import record_warmup_delivery_outcome

            record_warmup_delivery_outcome(
                provider_message_id=message_id,
                provider_status=str(record.get("provider_status") or ""),
                smtp_response=str(record.get("smtp_response") or ""),
            )
        except Exception:
            logger.exception(
                "mailopost_sender_warmup_feedback_failed",
                message_id=message_id,
            )
        if job_id:
            jobs.add(job_id)
            saved += 1
            try:
                from src.generator.delivery.suppression_store import upsert_from_provider_event
                from src.generator.delivery.send_guard import record_complaint

                upsert_from_provider_event(
                    recipient=str(record.get("recipient") or ""),
                    provider_status=str(record.get("provider_status") or ""),
                    source="webhook_mailopost",
                    job_id=job_id,
                    delivery_response=str(record.get("smtp_response") or ""),
                )
                from src.generator.delivery.channel_guard import record_channel_outcome

                record_channel_outcome(
                    connection_id=str(record.get("connection_id") or ""),
                    provider_message_id=message_id,
                    provider_status=str(record.get("provider_status") or ""),
                    recipient=str(record.get("recipient") or ""),
                    smtp_response=str(record.get("smtp_response") or ""),
                    occurred_at=str(record.get("occurred_at") or ""),
                )
                if str(record.get("provider_status") or "").strip().lower() in {"spam", "complaint", "complained"}:
                    record_complaint()
            except Exception:
                logger.exception(
                    "mailopost_delivery_feedback_failed",
                    message_id=message_id,
                    connection_id=str(record.get("connection_id") or ""),
                )
        else:
            unmatched += 1
    return {"saved": saved, "skipped": skipped, "duplicates": duplicates, "unmatched": unmatched, "jobs": sorted(jobs)}


def load_mailopost_events(job_id: str | None) -> list[dict[str, Any]]:
    return read_jsonl(mailopost_events_path(job_id))


def mailopost_events_path(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / EVENTS_FILENAME


def _unmatched_events_path() -> Path:
    return JOBS_DIR.parent / UNMATCHED_EVENTS_FILENAME


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("events", "items", "data", "messages"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    return [payload]


def _load_event_replay_keys(path: Path) -> set[str]:
    return {_event_replay_key(item) for item in read_jsonl(path)}


def _event_replay_key(record: dict[str, Any]) -> str:
    stored_key = _safe_text(record.get("event_key"))
    if stored_key:
        return stored_key
    event_id = _safe_text(record.get("event_id"))
    if event_id:
        return ":".join(("id", _safe_text(record.get("event_type")), _safe_text(record.get("message_id")), event_id))
    key_payload = {
        "event_type": _safe_text(record.get("event_type")),
        "message_id": _safe_text(record.get("message_id")),
        "recipient": _safe_text(record.get("recipient")).lower(),
        "occurred_at": _safe_text(record.get("occurred_at")),
        "event": record.get("event") if isinstance(record.get("event"), dict) else {},
    }
    raw = json.dumps(key_payload, ensure_ascii=False, sort_keys=True, default=str)
    return "hash:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _status_from_event(event_type: str) -> str:
    normalized = _safe_text(event_type).lower()
    return EVENT_STATUS_MAP.get(normalized, normalized)


def _extract_message_id(event: dict[str, Any]) -> str:
    nested = _nested_dicts(event)
    for data in (event, *nested):
        value = _extract_first_text(data, ("message_id", "messageId", "messageID", "email_id", "emailId", "id"))
        if value:
            return value
    return ""


def _extract_event_id(event: dict[str, Any]) -> str:
    nested = _nested_dicts(event)
    for data in (event, *nested):
        value = _extract_first_text(data, ("event_id", "eventId", "uuid"))
        if value:
            return value
    return ""


def _extract_event_type(event: dict[str, Any]) -> str:
    nested = _nested_dicts(event)
    for data in (event, *nested):
        value = _extract_first_text(data, ("event", "event_type", "eventType", "type", "status", "name"))
        if value:
            return value
    return ""


def _extract_delivery_response(event: dict[str, Any]) -> str:
    keys = (
        "smtpServerResponse",
        "smtp_server_response",
        "smtpResponse",
        "smtp_response",
        "deliveryResponse",
        "delivery_response",
        "bounceReason",
        "bounce_reason",
        "reason",
    )
    for data in (event, *_nested_dicts(event)):
        value = _extract_first_text(data, keys)
        if value:
            return value
    return ""


def _extract_email(event: dict[str, Any]) -> str:
    nested = _nested_dicts(event)
    for data in (event, *nested):
        value = _extract_first_text(data, ("email", "recipient", "to", "rcpt_to", "recipient_email"))
        if value:
            return value
    return ""


def _extract_occurred_at(event: dict[str, Any]) -> str:
    nested = _nested_dicts(event)
    for data in (event, *nested):
        value = _extract_first_text(data, ("occurred_at", "occurredAt", "created_at", "createdAt", "date", "timestamp"))
        if value:
            return value
    return ""


def _nested_dicts(event: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    nested = []
    for key in ("message", "email", "payload", "data", "event"):
        value = event.get(key)
        if isinstance(value, dict):
            nested.append(value)
    return tuple(nested)


def _extract_first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _load_message_job_index() -> dict[str, dict[str, str]]:
    from src.generator.delivery.provider_ids import provider_message_id_lookup_keys
    from src.jobs.job_docs import iter_sent_mail_items

    index: dict[str, dict[str, str]] = {}
    for storage_job_id, item in iter_sent_mail_items():
        if not isinstance(item, dict):
            continue
        provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
        if _safe_text(provider.get("provider") or item.get("transport")).lower() not in {"mailopost", ""}:
            provider_name = _safe_text(provider.get("provider")).lower()
            if provider_name != "mailopost":
                continue
        job_id = "" if storage_job_id == "__legacy__" else storage_job_id
        meta = {
            "job_id": job_id,
            "row_id": _safe_text(item.get("row_id")),
            "recipient": _safe_text(item.get("recipient")),
            "connection_id": _safe_text(item.get("connection_id")),
        }
        for raw in (
            item.get("provider_message_id"),
            item.get("message_id"),
            provider.get("message_id"),
            provider.get("id"),
        ):
            for message_id in provider_message_id_lookup_keys(raw):
                index[message_id] = meta
    return index


def _safe_text(value: Any) -> str:
    return str(value or "").strip()
