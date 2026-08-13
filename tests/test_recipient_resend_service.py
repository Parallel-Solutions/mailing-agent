from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.campaigns.recipient_resend_service import (
    enqueue_recipient_resend,
    get_recipient_resend_capability,
)


def _context(*, fallback: str = "backup@example.com"):
    campaign = SimpleNamespace(
        id="campaign-1",
        owner_username="admin",
        send_scenario="consent_then_materials",
    )
    recipient = SimpleNamespace(
        id=42,
        email="broken@example.com",
        email_fallback=fallback,
        extra={"delivery_email": "broken@example.com", "tried_emails": ["broken@example.com"]},
        excluded=False,
    )
    return campaign, recipient


def test_hard_bounce_uses_only_valid_fallback() -> None:
    campaign, recipient = _context()
    with (
        patch(
            "src.campaigns.recipient_resend_service._campaign_recipient",
            return_value=(campaign, recipient),
        ),
        patch("src.campaigns.recipient_resend_service._active_task", return_value=""),
        patch(
            "src.campaigns.recipient_resend_service.resolve_delivery_email",
            return_value=("backup@example.com", []),
        ) as resolve,
    ):
        result = get_recipient_resend_capability(
            job_id="job-1",
            row_id="42",
            manager_status="email_broken",
            failed_email="broken@example.com",
        )

    assert result["allowed"] is True
    assert result["mode"] == "fallback"
    assert result["target_email"] == "backup@example.com"
    assert resolve.call_args.kwargs["skip_emails"] == ["broken@example.com"]


def test_hard_bounce_without_fallback_is_blocked() -> None:
    campaign, recipient = _context(fallback="")
    with (
        patch(
            "src.campaigns.recipient_resend_service._campaign_recipient",
            return_value=(campaign, recipient),
        ),
        patch("src.campaigns.recipient_resend_service._active_task", return_value=""),
        patch(
            "src.campaigns.recipient_resend_service.resolve_delivery_email",
            return_value=(None, [{"error": "Адрес не найден"}]),
        ),
    ):
        result = get_recipient_resend_capability(
            job_id="job-1",
            row_id="42",
            manager_status="email_broken",
            failed_email="broken@example.com",
        )

    assert result["allowed"] is False
    assert result["state"] == "requires_new_email"


def test_soft_bounce_is_left_to_automatic_retry() -> None:
    campaign, recipient = _context()
    with (
        patch(
            "src.campaigns.recipient_resend_service._campaign_recipient",
            return_value=(campaign, recipient),
        ),
        patch("src.campaigns.recipient_resend_service._active_task", return_value=""),
        patch(
            "src.campaigns.recipient_resend_service._suppression",
            return_value=("soft_bounce", "2026-08-07T12:00:00+00:00"),
        ),
    ):
        result = get_recipient_resend_capability(
            job_id="job-1",
            row_id="42",
            manager_status="soft_bounce",
            failed_email="broken@example.com",
        )

    assert result["allowed"] is False
    assert result["state"] == "automatic_retry"
    assert result["retry_after"] == "2026-08-07T12:00:00+00:00"


def test_recent_final_error_waits_for_automatic_fallback() -> None:
    campaign, recipient = _context()
    with (
        patch(
            "src.campaigns.recipient_resend_service._campaign_recipient",
            return_value=(campaign, recipient),
        ),
        patch("src.campaigns.recipient_resend_service._active_task", return_value=""),
    ):
        result = get_recipient_resend_capability(
            job_id="job-1",
            row_id="42",
            manager_status="delivery_error",
            failed_email="broken@example.com",
            last_event_at="2099-08-01T12:00:00+00:00",
        )

    assert result["allowed"] is False
    assert result["state"] == "automatic_retry"
    assert result["retry_after"]
def test_active_resend_is_deduplicated() -> None:
    campaign, recipient = _context()
    with (
        patch(
            "src.campaigns.recipient_resend_service._campaign_recipient",
            return_value=(campaign, recipient),
        ),
        patch(
            "src.campaigns.recipient_resend_service._active_task",
            return_value="task-existing",
        ),
    ):
        result = get_recipient_resend_capability(
            job_id="job-1",
            row_id="42",
            manager_status="delivery_error",
            failed_email="broken@example.com",
        )

    assert result["state"] == "queued"
    assert result["task_id"] == "task-existing"


def test_enqueue_uses_recipient_scoped_active_key() -> None:
    capability = {
        "allowed": True,
        "target_email": "broken@example.com",
        "mode": "same",
        "reason": "ok",
        "retry_after": None,
        "state": "available",
        "campaign_id": "campaign-1",
        "recipient_id": 42,
        "send_scenario": "materials_now",
    }
    with (
        patch(
            "src.campaigns.recipient_resend_service.get_recipient_resend_capability",
            return_value=capability,
        ),
        patch(
            "src.campaigns.recipient_resend_service.enqueue_task",
            return_value=({"id": "task-1"}, True),
        ) as enqueue,
    ):
        result = enqueue_recipient_resend(
            job_id="job-1",
            row_id="42",
            manager_status="delivery_error",
            failed_email="broken@example.com",
            requested_by="admin",
        )

    assert result["state"] == "queued"
    assert result["task_id"] == "task-1"
    assert enqueue.call_args.kwargs["active_key"] == "recipient_resend:campaign-1:42"
    assert enqueue.call_args.kwargs["payload"]["send_run_id"].startswith("manual-resend-")
