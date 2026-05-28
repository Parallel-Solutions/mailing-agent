from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from src.jobs import resolve_job_paths


EVENTS_FILENAME = "unisender_go_events.jsonl"


def append_unisender_go_events(payload: Any) -> dict[str, Any]:
    """Persist UniSender Go webhook events grouped by our job metadata."""

    events = _extract_events(payload)
    saved = 0
    skipped = 0
    jobs: set[str] = set()

    for event in events:
        job_id = _extract_job_id(event)
        if not job_id:
            skipped += 1
            continue
        record = {
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "event_type": _extract_event_type(event),
            "recipient": _extract_recipient(event),
            "provider_job_id": _extract_first_text(event, ("job_id", "message_id", "email_id", "id")),
            "row_id": _extract_metadata(event).get("app_row_id") or _extract_metadata(event).get("row_id") or "",
            "mun_name": _extract_metadata(event).get("app_mun_name") or _extract_metadata(event).get("mun_name") or "",
        }
        path = unisender_go_events_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        saved += 1
        jobs.add(job_id)

    return {
        "saved": saved,
        "skipped": skipped,
        "jobs": sorted(jobs),
    }


def load_unisender_go_events(job_id: str | None) -> list[dict[str, Any]]:
    path = unisender_go_events_path(job_id)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def unisender_go_events_path(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / EVENTS_FILENAME


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("events", "event", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    return [payload]


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
        value = event.get(key)
        if isinstance(value, dict):
            return value
    message = event.get("message")
    if isinstance(message, dict):
        for key in ("metadata", "global_metadata"):
            value = message.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _extract_event_type(event: dict[str, Any]) -> str:
    return _extract_first_text(event, ("event_type", "event", "type", "status", "name"))


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
