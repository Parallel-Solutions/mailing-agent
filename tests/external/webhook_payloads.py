"""Factories for provider webhook payloads used in external tests.

These payloads mirror what real providers send to our webhook endpoints.
Used for:
  - Level 1 preflight (simulated webhook after real send)
  - Idempotency tests (replay same payload twice)
  - Fallback when real provider webhook doesn't arrive in time
"""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# RuSender payloads
# ---------------------------------------------------------------------------


def rusender_event(trigger: str, task_id: str, *, email: str = "", url: str = "") -> dict:
    """Single RuSender event dict (used inside events list)."""
    ev: dict = {
        "trigger": trigger,
        "task_id": task_id,
        "created_at": _now_iso(),
    }
    if email:
        ev["email"] = email
    if url:
        ev["url"] = url
    return ev


def rusender_payload(trigger: str, task_id: str, *, email: str = "", url: str = "") -> dict:
    """Full RuSender webhook payload: {"events": [...]}."""
    return {"events": [rusender_event(trigger, task_id, email=email, url=url)]}


def rusender_delivered(task_id: str, *, email: str = "") -> dict:
    return rusender_payload("external_mail.delivered", task_id, email=email)


def rusender_opened(task_id: str, *, email: str = "") -> dict:
    return rusender_payload("external_mail.open", task_id, email=email)


def rusender_clicked(task_id: str, *, email: str = "", url: str = "https://example.com") -> dict:
    return rusender_payload("external_mail.click", task_id, email=email, url=url)


def rusender_hard_bounced(task_id: str, *, email: str = "") -> dict:
    return rusender_payload("external_mail.hard_bounced", task_id, email=email)


def rusender_soft_bounced(task_id: str, *, email: str = "") -> dict:
    return rusender_payload("external_mail.soft_bounced", task_id, email=email)


def rusender_unsubscribed(task_id: str, *, email: str = "") -> dict:
    return rusender_payload("external_mail.unsubscribe", task_id, email=email)


def rusender_complaint(task_id: str, *, email: str = "") -> dict:
    return rusender_payload("external_mail.complaint", task_id, email=email)


def rusender_failed(task_id: str, *, email: str = "") -> dict:
    return rusender_payload("external_mail.error", task_id, email=email)


# ---------------------------------------------------------------------------
# MailoPost payloads
# ---------------------------------------------------------------------------


def mailopost_event(event: str, message_id: str, *, email: str = "", url: str = "") -> dict:
    ev: dict = {
        "event": event,
        "message_id": message_id,
        "at": _now_iso(),
    }
    if email:
        ev["email"] = email
        ev["to"] = email
    if url:
        ev["url"] = url
    return ev


def mailopost_payload(event: str, message_id: str, *, email: str = "", url: str = "") -> dict:
    """MailoPost wraps events in a list."""
    return [mailopost_event(event, message_id, email=email, url=url)]


def mailopost_delivered(message_id: str, *, email: str = "") -> dict:
    return mailopost_payload("delivered", message_id, email=email)


def mailopost_opened(message_id: str, *, email: str = "") -> dict:
    return mailopost_payload("opened", message_id, email=email)


def mailopost_clicked(message_id: str, *, email: str = "", url: str = "https://example.com") -> dict:
    return mailopost_payload("clicked", message_id, email=email, url=url)


def mailopost_hard_bounced(message_id: str, *, email: str = "") -> dict:
    return mailopost_payload("hard_bounced", message_id, email=email)


def mailopost_soft_bounced(message_id: str, *, email: str = "") -> dict:
    return mailopost_payload("soft_bounced", message_id, email=email)


def mailopost_unsubscribed(message_id: str, *, email: str = "") -> dict:
    return mailopost_payload("unsubscribed", message_id, email=email)


def mailopost_complaint(message_id: str, *, email: str = "") -> dict:
    return mailopost_payload("complained", message_id, email=email)


# ---------------------------------------------------------------------------
# UniSender Go payloads
# ---------------------------------------------------------------------------


def unisender_go_event(
    event_name: str,
    job_id: str,
    *,
    email: str = "",
    app_job_id: str = "",
    url: str = "",
) -> dict:
    ev: dict = {
        "event_name": event_name,
        "event_time": _now_iso(),
        "job_id": job_id,
        "email": email,
        "metadata": {},
    }
    if app_job_id:
        ev["metadata"]["app_job_id"] = app_job_id
    if url:
        ev["url"] = url
    return ev


def unisender_go_payload(
    event_name: str,
    job_id: str,
    *,
    email: str = "",
    app_job_id: str = "",
    url: str = "",
) -> dict:
    return {"events": [unisender_go_event(event_name, job_id, email=email, app_job_id=app_job_id, url=url)]}


def unisender_go_delivered(job_id: str, *, email: str = "", app_job_id: str = "") -> dict:
    return unisender_go_payload("delivered", job_id, email=email, app_job_id=app_job_id)


def unisender_go_opened(job_id: str, *, email: str = "", app_job_id: str = "") -> dict:
    return unisender_go_payload("opened", job_id, email=email, app_job_id=app_job_id)


def unisender_go_clicked(job_id: str, *, email: str = "", app_job_id: str = "", url: str = "https://example.com") -> dict:
    return unisender_go_payload("clicked", job_id, email=email, app_job_id=app_job_id, url=url)


def unisender_go_hard_bounced(job_id: str, *, email: str = "", app_job_id: str = "") -> dict:
    return unisender_go_payload("hard_bounced", job_id, email=email, app_job_id=app_job_id)


def unisender_go_soft_bounced(job_id: str, *, email: str = "", app_job_id: str = "") -> dict:
    return unisender_go_payload("soft_bounced", job_id, email=email, app_job_id=app_job_id)


def unisender_go_unsubscribed(job_id: str, *, email: str = "", app_job_id: str = "") -> dict:
    return unisender_go_payload("unsubscribed", job_id, email=email, app_job_id=app_job_id)


def unisender_go_spam(job_id: str, *, email: str = "", app_job_id: str = "") -> dict:
    return unisender_go_payload("spam", job_id, email=email, app_job_id=app_job_id)
