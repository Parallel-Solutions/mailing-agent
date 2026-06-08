from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from src.jobs import resolve_job_paths
from src.utils.config import settings


CONSENT_FILENAME = "consents.json"
CONSENT_TEXT = "Согласен получить коммерческое предложение и проект договора от ООО «Параллельные Решения»."


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _consent_path(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / CONSENT_FILENAME


def _load_records(job_id: str | None) -> list[dict[str, Any]]:
    path = _consent_path(job_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    records = data.get("records") if isinstance(data, dict) else None
    return [item for item in records if isinstance(item, dict)] if isinstance(records, list) else []


def _save_records(job_id: str | None, records: list[dict[str, Any]]) -> None:
    path = _consent_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _record_matches(record: dict[str, Any], *, row_id: Any, recipient: str) -> bool:
    return (
        _safe_text(record.get("row_id")) == _safe_text(row_id)
        and _safe_text(record.get("recipient")).lower() == _safe_text(recipient).lower()
    )


def public_consent_url(token: str) -> str:
    base_url = _safe_text(settings.public_base_url).rstrip("/")
    if not base_url:
        base_url = "http://127.0.0.1:8000"
    return f"{base_url}/consent/request/{token}"


def prepare_consent_request(
    *,
    job_id: str | None,
    row: dict[str, Any],
    recipient: str,
    transport: str,
) -> dict[str, Any]:
    records = _load_records(job_id)
    row_id = row.get("ID")
    now = datetime.now().isoformat(timespec="seconds")
    for record in records:
        if _record_matches(record, row_id=row_id, recipient=recipient):
            record.setdefault("token", secrets.token_urlsafe(24))
            record["status"] = _safe_text(record.get("status")) or "pending"
            record["last_request_prepared_at"] = now
            record["transport"] = _safe_text(transport)
            _save_records(job_id, records)
            return dict(record, consent_url=public_consent_url(record["token"]))

    token = secrets.token_urlsafe(24)
    record = {
        "token": token,
        "status": "pending",
        "job_id": _safe_text(job_id),
        "row_id": _safe_text(row_id),
        "mun_name": _safe_text(row.get("MUN_NAME")),
        "recipient": _safe_text(recipient),
        "consent_text": CONSENT_TEXT,
        "created_at": now,
        "last_request_prepared_at": now,
        "transport": _safe_text(transport),
    }
    records.append(record)
    _save_records(job_id, records)
    return dict(record, consent_url=public_consent_url(token))


def mark_consent_request_sent(
    *,
    job_id: str | None,
    row_id: Any,
    recipient: str,
    provider: dict[str, Any] | None = None,
) -> None:
    records = _load_records(job_id)
    now = datetime.now().isoformat(timespec="seconds")
    for record in records:
        if _record_matches(record, row_id=row_id, recipient=recipient):
            record["status"] = "request_sent"
            record["request_sent_at"] = now
            if provider:
                record["provider"] = provider
            _save_records(job_id, records)
            return


def has_confirmed_consent(*, job_id: str | None, row_id: Any, recipient: str) -> bool:
    for record in _load_records(job_id):
        if _record_matches(record, row_id=row_id, recipient=recipient):
            return _safe_text(record.get("status")) == "confirmed"
    return False


def get_consent_by_token(token: str) -> dict[str, Any] | None:
    clean_token = _safe_text(token)
    for job_dir in _iter_job_dirs():
        job_id = job_dir.name if job_dir.name.startswith("job-") else None
        for record in _load_records(job_id):
            if _safe_text(record.get("token")) == clean_token:
                return dict(record)
    return None


def confirm_consent(token: str, *, ip: str = "", user_agent: str = "") -> dict[str, Any] | None:
    clean_token = _safe_text(token)
    for job_dir in _iter_job_dirs():
        job_id = job_dir.name if job_dir.name.startswith("job-") else None
        records = _load_records(job_id)
        changed = False
        for record in records:
            if _safe_text(record.get("token")) != clean_token:
                continue
            record["status"] = "confirmed"
            record["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
            record["confirmed_ip"] = _safe_text(ip)
            record["confirmed_user_agent"] = _safe_text(user_agent)
            record["materials_dispatch_requested_at"] = record["confirmed_at"]
            changed = True
            if changed:
                _save_records(job_id, records)
            return dict(record)
    return None


def _iter_job_dirs() -> list[Path]:
    jobs_root = resolve_job_paths("job-placeholder").root_dir.parent
    candidates: list[Path] = []
    if jobs_root.exists():
        candidates.extend(path for path in jobs_root.iterdir() if path.is_dir())
    legacy_root = resolve_job_paths(None).root_dir
    if legacy_root.exists():
        candidates.append(legacy_root)
    return candidates
