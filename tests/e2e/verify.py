from __future__ import annotations

from typing import Any

try:
    from src.jobs.job_docs import read_sent_mail_log
except ImportError:  # pragma: no cover
    read_sent_mail_log = None  # type: ignore[assignment]

try:
    from tests.e2e.consent_helpers import load_consent_records
except ImportError:  # pragma: no cover
    load_consent_records = None  # type: ignore[assignment]

EXPECTED_RECIPIENT_COUNT = 2

_BLOCKED_RESULT_PREFIXES = ("blocked", "error", "needs_")


def collect_sender_rows(status: dict[str, Any]) -> list[dict[str, Any]]:
    rows = status.get("rows") or []
    return [row for row in rows if isinstance(row, dict)]


def collect_sent_mail_log(job_id: str) -> list[dict[str, Any]]:
    if read_sent_mail_log is None:
        return []
    items = read_sent_mail_log(job_id)
    return [dict(item) for item in items if isinstance(item, dict)]


def extract_delivery_rows(
    *,
    job_id: str,
    sender_status: dict[str, Any],
    analytics: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in collect_sender_rows(sender_status):
        rows.append(
            {
                "job_id": job_id,
                "row_id": str(row.get("id") or row.get("row_id") or ""),
                "recipient": str(row.get("recipient") or ""),
                "result": str(row.get("result") or ""),
                "provider": str(row.get("provider") or sender_status.get("transport") or ""),
                "message_id": str(row.get("message_id") or row.get("provider_message_id") or ""),
                "error": str(row.get("error") or row.get("warning") or ""),
                "source": "sender_status",
            }
        )

    for item in collect_sent_mail_log(job_id):
        rows.append(
            {
                "job_id": job_id,
                "row_id": str(item.get("row_id") or item.get("id") or ""),
                "recipient": str(item.get("recipient") or item.get("email") or ""),
                "result": str(item.get("result") or item.get("status") or "sent"),
                "provider": str(item.get("provider") or item.get("transport") or ""),
                "message_id": str(item.get("message_id") or item.get("provider_message_id") or ""),
                "error": str(item.get("error") or ""),
                "source": "sent_mail_log",
            }
        )

    if analytics:
        events = analytics.get("events") or analytics.get("recent_events") or []
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                rows.append(
                    {
                        "job_id": job_id,
                        "row_id": str(event.get("row_id") or ""),
                        "recipient": str(event.get("recipient") or event.get("email") or ""),
                        "result": str(event.get("event") or event.get("status") or ""),
                        "provider": "rusender",
                        "message_id": str(event.get("message_id") or event.get("id") or ""),
                        "error": "",
                        "source": "analytics",
                    }
                )
    return rows


def _row_result_is_failure(result: str) -> bool:
    normalized = result.strip().lower()
    if not normalized:
        return False
    if normalized in {"sent", "skipped_logged_sent"}:
        return False
    if normalized == "skipped_duplicate":
        return False
    return normalized.startswith(_BLOCKED_RESULT_PREFIXES) or normalized == "error"


def _count_successful_sender_rows(sender_status: dict[str, Any], *, send_mode: str) -> int:
    success = 0
    for row in collect_sender_rows(sender_status):
        result = str(row.get("result") or "").strip().lower()
        error = str(row.get("error") or "").strip()
        if error:
            continue
        if result in {"sent", "skipped_logged_sent"}:
            success += 1
            continue
        if result == "skipped_duplicate" and send_mode == "materials":
            success += 1
    return success


def validate_per_recipient_rows(
    delivery_rows: list[dict[str, Any]],
    *,
    send_mode: str,
) -> tuple[bool, str]:
    sender_rows = [row for row in delivery_rows if row.get("source") == "sender_status" and row.get("recipient")]
    if not sender_rows:
        return False, "no per-recipient sender rows in delivery report"

    failures: list[str] = []
    successes = 0
    for row in sender_rows:
        recipient = str(row.get("recipient") or "")
        result = str(row.get("result") or "")
        error = str(row.get("error") or "").strip()
        if error or _row_result_is_failure(result):
            failures.append(f"{recipient or '?'}: {error or result}")
            continue
        if result in {"sent", "skipped_logged_sent"} or (
            result == "skipped_duplicate" and send_mode == "materials"
        ):
            successes += 1

    if failures:
        return False, f"recipient failures: {'; '.join(failures)}"
    if successes < EXPECTED_RECIPIENT_COUNT:
        return False, f"expected {EXPECTED_RECIPIENT_COUNT} successful recipients, got {successes}"
    return True, f"all {successes} recipients ok"


def classify_send_success(
    sender_status: dict[str, Any],
    *,
    send_mode: str,
    dry_run: bool,
    expected_row_count: int = EXPECTED_RECIPIENT_COUNT,
) -> tuple[bool, str]:
    status = str(sender_status.get("status") or "").lower()
    mode = str(sender_status.get("mode") or "").lower()
    if status != "completed":
        return False, f"sender status={status}"
    if dry_run and mode != "dry_run":
        return False, f"expected dry_run mode, got {mode}"
    if not dry_run and mode != "send":
        return False, f"expected send mode, got {mode}"

    error_rows = int(sender_status.get("error_rows") or 0)
    sent_rows = int(sender_status.get("sent_rows") or 0)
    skipped_rows = int(sender_status.get("skipped_rows") or 0)
    ready_rows = int(sender_status.get("ready_rows") or 0)

    if dry_run:
        if ready_rows > 0 or sent_rows >= 0:
            return True, "dry_run completed"
        return False, "dry_run completed without ready rows"

    successful_rows = _count_successful_sender_rows(sender_status, send_mode=send_mode)
    effective_success = max(sent_rows, successful_rows)
    if send_mode == "materials":
        effective_success = max(effective_success, sent_rows + skipped_rows)

    if error_rows > 0:
        return False, f"{send_mode} has {error_rows} error_rows"

    if effective_success < expected_row_count:
        return (
            False,
            f"{send_mode} expected {expected_row_count} successful rows, "
            f"got sent={sent_rows} skipped={skipped_rows} per_row={successful_rows}",
        )

    return True, f"{send_mode} sent ({effective_success}/{expected_row_count})"


def verify_consent_materials_dispatch(job_id: str, *, expected_count: int = EXPECTED_RECIPIENT_COUNT) -> tuple[bool, str]:
    if load_consent_records is None:
        return False, "consent_store unavailable"
    records = load_consent_records(job_id)
    recipients_expected = {
        str(record.get("recipient") or "").strip().lower()
        for record in records
        if str(record.get("recipient") or "").strip()
    }
    if not recipients_expected:
        return False, "no consent recipients found"
    sent = [
        record
        for record in records
        if str(record.get("materials_status") or "").lower() == "sent"
        and str(record.get("recipient") or "").strip().lower() in recipients_expected
    ]
    target = max(expected_count, len(recipients_expected))
    if len(sent) >= target:
        return True, f"materials auto-dispatched ({len(sent)}/{target})"
    return False, f"materials auto-dispatch incomplete ({len(sent)}/{target})"
