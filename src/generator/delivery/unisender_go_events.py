from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from src.jobs import resolve_job_paths
from src.jobs.json_store import append_jsonl, path_lock, read_jsonl


EVENTS_FILENAME = "unisender_go_events.jsonl"


def append_unisender_go_events(payload: Any) -> dict[str, Any]:
    """Persist UniSender Go webhook events grouped by our job metadata."""

    events = _extract_events(payload)
    saved = 0
    skipped = 0
    duplicates = 0
    jobs: set[str] = set()
    existing_keys_by_path: dict[Path, set[str]] = {}

    for event in events:
        job_id = _extract_job_id(event)
        if not job_id:
            skipped += 1
            continue
        record = {
            "received_at": _extract_event_time(event) or datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "event_type": _extract_event_type(event),
            "recipient": _extract_recipient(event),
            "provider_job_id": _extract_first_text(event, ("job_id", "message_id", "email_id", "id")),
            "row_id": _extract_metadata(event).get("app_row_id") or _extract_metadata(event).get("row_id") or "",
            "mun_name": _extract_metadata(event).get("app_mun_name") or _extract_metadata(event).get("mun_name") or "",
        }
        path = unisender_go_events_path(job_id)
        with path_lock(path):
            existing_keys = existing_keys_by_path.setdefault(path, _load_event_replay_keys(path))
            event_key = _event_replay_key(record)
            if event_key in existing_keys:
                duplicates += 1
                jobs.add(job_id)
                continue
            record["event_key"] = event_key
            append_jsonl(path, record)
            existing_keys.add(event_key)
        saved += 1
        jobs.add(job_id)
    return {
        "saved": saved,
        "skipped": skipped,
        "duplicates": duplicates,
        "jobs": sorted(jobs),
    }


def _load_event_replay_keys(path: Path) -> set[str]:
    return {_event_replay_key(item) for item in read_jsonl(path)}

def _event_replay_key(record: dict[str, Any]) -> str:
    stored_key = _safe_text(record.get("event_key"))
    if stored_key:
        return stored_key
    event = record.get("event") if isinstance(record.get("event"), dict) else {}
    explicit_id = _extract_first_text(event, ("event_id", "eventId", "uuid"))
    if explicit_id:
        return ":".join(
            (
                "id",
                _safe_text(record.get("event_type")),
                _safe_text(record.get("recipient")).lower(),
                _safe_text(explicit_id),
            )
        )
    key_payload = {
        "event_type": _safe_text(record.get("event_type")),
        "recipient": _safe_text(record.get("recipient")).lower(),
        "provider_job_id": _safe_text(record.get("provider_job_id")),
        "row_id": _safe_text(record.get("row_id")),
        "event": event,
    }
    raw = json.dumps(key_payload, ensure_ascii=False, sort_keys=True, default=str)
    return "hash:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_text(value: Any) -> str:
    return str(value or "").strip()

def load_unisender_go_events(job_id: str | None) -> list[dict[str, Any]]:
    return read_jsonl(unisender_go_events_path(job_id))

def unisender_go_events_path(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / EVENTS_FILENAME


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [_unwrap_event(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    nested = _extract_events_by_user(payload)
    if nested:
        return nested
    for key in ("events", "event", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [_unwrap_event(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [_unwrap_event(value)]
    return [_unwrap_event(payload)]


def _extract_events_by_user(payload: dict[str, Any]) -> list[dict[str, Any]]:
    users = payload.get("events_by_user")
    if not isinstance(users, list):
        return []
    events: list[dict[str, Any]] = []
    for user in users:
        if not isinstance(user, dict):
            continue
        user_events = user.get("events")
        if not isinstance(user_events, list):
            continue
        for event in user_events:
            if isinstance(event, dict):
                events.append(_unwrap_event(event))
    return events


def _unwrap_event(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("event_data")
    if not isinstance(data, dict):
        return event
    unwrapped = dict(data)
    event_name = _extract_first_text(event, ("event_name",))
    if event_name and "event_name" not in unwrapped:
        unwrapped["event_name"] = event_name
    return unwrapped


def _extract_job_id(event: dict[str, Any]) -> str:
    metadata = _extract_metadata(event)
    return str(
        metadata.get("app_job_id")
        or metadata.get("job_id")
        or metadata.get("mailing_agent_job_id")
        or ""
    ).strip()


def _extract_metadata(event: dict[str, Any]) -> dict[str, Any]:
    for key in ("metadata", "global_metadata", "message_metadata"):
        metadata = _coerce_metadata(event.get(key))
        if metadata:
            return metadata
    message = event.get("message")
    if isinstance(message, dict):
        for key in ("metadata", "global_metadata"):
            metadata = _coerce_metadata(message.get(key))
            if metadata:
                return metadata
    return {}


def _coerce_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_event_type(event: dict[str, Any]) -> str:
    direct = _extract_first_text(event, ("event_type", "event", "type", "status", "name"))
    if direct:
        return direct
    delivery_info = event.get("delivery_info")
    if isinstance(delivery_info, dict):
        return _extract_first_text(delivery_info, ("delivery_status", "status"))
    return ""


def _extract_recipient(event: dict[str, Any]) -> str:
    direct = _extract_first_text(event, ("email", "recipient", "to"))
    if direct:
        return direct
    recipient = event.get("recipient")
    if isinstance(recipient, dict):
        return _extract_first_text(recipient, ("email", "address"))
    return ""


def _extract_first_text(event: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = event.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _extract_event_time(event: dict[str, Any]) -> str:
    return _extract_first_text(event, ("event_time", "time", "timestamp", "created_at", "date"))
