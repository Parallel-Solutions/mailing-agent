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


EVENTS_FILENAME = "rusender_events.jsonl"
UNMATCHED_EVENTS_FILENAME = "rusender_events_unmatched.jsonl"

TRIGGER_STATUS_MAP = {
    "external_mail.delivered": "delivered",
    "external_mail.hard_bounced": "hard_bounced",
    "external_mail.soft_bounced": "soft_bounced",
    "external_mail.error": "err_delivery_failed",
    "external_mail.open": "opened",
    "external_mail.click": "clicked",
    "external_mail.unsubscribe": "unsubscribed",
    "external_mail.complaint": "spam",
}


def append_rusender_events(payload: Any) -> dict[str, Any]:
    events = _extract_events(payload)
    task_to_job = _load_task_job_index()
    saved = 0
    skipped = 0
    duplicates = 0
    unmatched = 0
    jobs: set[str] = set()
    existing_keys_by_path: dict[Path, set[str]] = {}

    for event in events:
        task_id = _extract_task_id(event)
        if not task_id:
            skipped += 1
            continue

        task_info = task_to_job.get(task_id, {})
        job_id = task_info.get("job_id", "")
        record = {
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "event_id": _extract_first_text(event, ("eventId", "event_id", "id")),
            "event_type": _extract_trigger(event),
            "provider_status": _status_from_trigger(_extract_trigger(event)),
            "occurred_at": _extract_first_text(event, ("occurredAt", "occurred_at", "createdAt", "created_at")),
            "task_id": task_id,
            "recipient": _extract_email(event) or task_info.get("recipient", ""),
            "connection_id": task_info.get("connection_id", ""),
            "smtp_response": _extract_smtp_response(event),
            "event": event,
        }

        path = rusender_events_path(job_id) if job_id else _unmatched_events_path()
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
                provider_message_id=task_id,
                provider_status=str(record.get("provider_status") or ""),
                smtp_response=str(record.get("smtp_response") or ""),
            )
        except Exception:
            logger.exception(
                "rusender_sender_warmup_feedback_failed",
                task_id=task_id,
            )
        if job_id:
            jobs.add(job_id)
            saved += 1
            try:
                from src.generator.delivery.suppression_store import upsert_from_provider_event

                upsert_from_provider_event(
                    recipient=str(record.get("recipient") or ""),
                    provider_status=str(record.get("provider_status") or ""),
                    source="webhook_rusender",
                    job_id=job_id,
                    delivery_response=str(record.get("smtp_response") or ""),
                )
                from src.generator.delivery.channel_guard import record_channel_outcome

                record_channel_outcome(
                    connection_id=str(record.get("connection_id") or ""),
                    provider_message_id=task_id,
                    provider_status=str(record.get("provider_status") or ""),
                    recipient=str(record.get("recipient") or ""),
                    smtp_response=str(record.get("smtp_response") or ""),
                    occurred_at=str(record.get("occurred_at") or ""),
                )
            except Exception:
                logger.exception(
                    "rusender_delivery_feedback_failed",
                    task_id=task_id,
                    connection_id=str(record.get("connection_id") or ""),
                )
        else:
            unmatched += 1
    return {"saved": saved, "skipped": skipped, "duplicates": duplicates, "unmatched": unmatched, "jobs": sorted(jobs)}


def _load_event_replay_keys(path: Path) -> set[str]:
    return {_event_replay_key(item) for item in read_jsonl(path)}

def _event_replay_key(record: dict[str, Any]) -> str:
    stored_key = _safe_text(record.get("event_key"))
    if stored_key:
        return stored_key
    event_id = _safe_text(record.get("event_id"))
    if event_id:
        return ":".join(
            (
                "id",
                _safe_text(record.get("event_type")),
                _safe_text(record.get("task_id")),
                _safe_text(record.get("recipient")).lower(),
                event_id,
            )
        )
    event = record.get("event") if isinstance(record.get("event"), dict) else {}
    key_payload = {
        "event_type": _safe_text(record.get("event_type")),
        "task_id": _safe_text(record.get("task_id")),
        "recipient": _safe_text(record.get("recipient")).lower(),
        "occurred_at": _safe_text(record.get("occurred_at")),
        "event": event,
    }
    raw = json.dumps(key_payload, ensure_ascii=False, sort_keys=True, default=str)
    return "hash:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_text(value: Any) -> str:
    return str(value or "").strip()

def load_rusender_events(job_id: str | None) -> list[dict[str, Any]]:
    return read_jsonl(rusender_events_path(job_id))

def rusender_events_path(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / EVENTS_FILENAME


def _unmatched_events_path() -> Path:
    return JOBS_DIR.parent / UNMATCHED_EVENTS_FILENAME


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    if isinstance(events, list):
        return [item for item in events if isinstance(item, dict)]
    if isinstance(events, dict):
        return [events]
    return [payload]


def _extract_trigger(event: dict[str, Any]) -> str:
    return _extract_first_text(event, ("trigger", "event", "type", "name"))


def _status_from_trigger(trigger: str) -> str:
    return TRIGGER_STATUS_MAP.get(str(trigger or "").strip(), str(trigger or "").strip())


def _extract_task_id(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        task_id = _extract_first_text(
            payload,
            ("taskId", "task_id", "uuid", "message_id", "idempotencyKey", "idempotency_key"),
        )
        if task_id:
            return task_id
    return _extract_first_text(
        event,
        ("taskId", "task_id", "uuid", "message_id", "idempotencyKey", "idempotency_key"),
    )


def _extract_email(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        email = _extract_first_text(payload, ("email", "recipient", "to"))
        if email:
            return email
    return _extract_first_text(event, ("email", "recipient", "to"))


def _extract_smtp_response(event: dict[str, Any]) -> str:
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
    payload = event.get("payload")
    if isinstance(payload, dict):
        response = _extract_first_text(payload, keys)
        if response:
            return response
    return _extract_first_text(event, keys)


def _extract_first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _load_task_job_index() -> dict[str, dict[str, str]]:
    from src.generator.delivery.provider_ids import provider_message_id_lookup_keys
    from src.jobs.job_docs import iter_sent_mail_items

    index: dict[str, dict[str, str]] = {}
    for job_id, item in iter_sent_mail_items():
        provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
        raw_ids = [
            item.get("provider_message_id"),
            item.get("message_id"),
            item.get("provider_job_id"),
            provider.get("message_id"),
            provider.get("uuid"),
            provider.get("idempotency_key"),
            provider.get("idempotencyKey"),
            item.get("idempotency_key"),
            item.get("idempotencyKey"),
        ]
        meta = {
            "job_id": "" if job_id == "__legacy__" else job_id,
            "row_id": str(item.get("row_id") or "").strip(),
            "recipient": str(item.get("recipient") or "").strip(),
            "connection_id": str(item.get("connection_id") or "").strip(),
        }
        for raw in raw_ids:
            for task_id in provider_message_id_lookup_keys(raw):
                index[task_id] = meta
    return index
