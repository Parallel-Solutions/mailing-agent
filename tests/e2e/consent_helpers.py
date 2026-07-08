from __future__ import annotations

import time
from typing import Any

from tests.e2e.api_client import E2EApiClient
from tests.e2e.config import E2EConfig

try:
    from src.generator.delivery.consent_store import _load_records
except ImportError:  # pragma: no cover - host-only fallback
    _load_records = None  # type: ignore[assignment]


def load_consent_records(job_id: str) -> list[dict[str, Any]]:
    if _load_records is None:
        raise RuntimeError(
            "consent_store is unavailable. Run the matrix inside the app container "
            "or from the project venv with PYTHONPATH set."
        )
    records = _load_records(job_id)
    return [dict(item) for item in records if isinstance(item, dict)]


def consent_tokens_for_send(
    job_id: str,
    *,
    statuses: tuple[str, ...] = ("request_sent", "pending"),
) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for record in load_consent_records(job_id):
        status = str(record.get("status") or "").strip().lower()
        token = str(record.get("token") or "").strip()
        if not token:
            continue
        if status in statuses:
            tokens.append(record)
    return tokens


def wait_materials_sent(
    job_id: str,
    *,
    timeout_seconds: float,
    recipients: set[str] | None = None,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_records: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last_records = load_consent_records(job_id)
        matched = [
            record
            for record in last_records
            if str(record.get("materials_status") or "").lower() == "sent"
            and (not recipients or str(record.get("recipient") or "").strip().lower() in recipients)
        ]
        if recipients:
            if len(matched) >= len(recipients):
                return matched
        elif matched:
            return matched
        time.sleep(2.0)
    return [
        record
        for record in last_records
        if str(record.get("materials_status") or "").lower() == "sent"
    ]


def ensure_confirmed_consent_for_materials(
    api: E2EApiClient,
    job_id: str,
    config: E2EConfig,
) -> list[dict[str, Any]]:
    """Confirm any pending/request_sent consent records so materials send can proceed."""
    confirmed: list[dict[str, Any]] = []
    for record in load_consent_records(job_id):
        status = str(record.get("status") or "").strip().lower()
        token = str(record.get("token") or "").strip()
        if not token:
            continue
        if status == "confirmed":
            confirmed.append(record)
            continue
        if status in {"pending", "request_sent"}:
            response = api.confirm_consent(token)
            if response.status_code not in {200, 410}:
                raise RuntimeError(f"Consent confirm failed ({response.status_code}) for token={token[:8]}...")
            wait_materials_sent(
                job_id,
                timeout_seconds=config.consent_timeout_seconds,
                recipients={str(record.get("recipient") or "").strip().lower()},
            )
            confirmed.append(record)
    return confirmed


def run_consent_flow(
    api: E2EApiClient,
    job_id: str,
    config: E2EConfig,
) -> list[dict[str, Any]]:
    tokens = consent_tokens_for_send(job_id)
    if not tokens:
        # Give sender a moment to persist consent records.
        time.sleep(2.0)
        tokens = consent_tokens_for_send(job_id)
    confirmed: list[dict[str, Any]] = []
    for record in tokens:
        token = str(record.get("token") or "").strip()
        if not token:
            continue
        response = api.confirm_consent(token)
        if response.status_code not in {200, 410}:
            raise RuntimeError(f"Consent confirm failed ({response.status_code}) for token={token[:8]}...")
        confirmed.append(record)
    recipients = {
        str(record.get("recipient") or "").strip().lower()
        for record in confirmed
        if str(record.get("recipient") or "").strip()
    }
    wait_materials_sent(
        job_id,
        timeout_seconds=config.consent_timeout_seconds,
        recipients=recipients or None,
    )
    return confirmed
