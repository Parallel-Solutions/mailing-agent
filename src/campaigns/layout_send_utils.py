"""Shared helpers for KP layout failures during campaign send."""

from __future__ import annotations

from typing import Any

from src.campaigns.service import record_delivery_attempt
from src.generator.generation.kp_one_page_fitter import KpLayoutError, LAYOUT_ERROR_CODE
from src.infra.models import Campaign, CampaignRecipient


def apply_kp_layout_failure(recipient: CampaignRecipient, error: KpLayoutError) -> None:
    extra = dict(recipient.extra or {})
    extra["layout_error_code"] = str(error.layout_error_code or LAYOUT_ERROR_CODE)
    recipient.extra = extra
    recipient.last_error = str(error)
    recipient.send_status = "failed"


def append_kp_layout_sent_mail_log(
    *,
    campaign: Campaign,
    recipient: CampaignRecipient,
    error: KpLayoutError | str,
    subject: str = "",
    send_mode: str = "",
    transport: str = "smtp",
) -> None:
    job_id = campaign.job_id
    if not job_id:
        return
    message = str(error)
    layout_error_code = LAYOUT_ERROR_CODE
    if isinstance(error, KpLayoutError):
        layout_error_code = str(error.layout_error_code or LAYOUT_ERROR_CODE)
    try:
        from datetime import datetime, timezone

        from src.jobs.job_docs import append_event

        append_event(
            job_id,
            "sent_mail_log",
            {
                "email": recipient.email,
                "recipient": recipient.email,
                "organization": recipient.company,
                "mun_name": recipient.company,
                "row_id": str(
                    (recipient.row_index + 1) if recipient.row_index is not None else recipient.id
                ),
                "status": "failed",
                "error": message,
                "layout_error_code": layout_error_code,
                "transport": transport or campaign.transport or "smtp",
                "campaign_name": campaign.name,
                "campaign_id": campaign.id,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "subject": subject,
                "send_mode": send_mode,
            },
        )
    except Exception:
        pass


def record_kp_layout_send_failure(
    *,
    campaign_id: str,
    recipient: CampaignRecipient,
    campaign: Campaign,
    batch_id: str | None,
    error: KpLayoutError,
    subject: str = "",
    send_mode: str = "",
) -> None:
    apply_kp_layout_failure(recipient, error)
    if batch_id:
        record_delivery_attempt(
            campaign_id=campaign_id,
            recipient_id=int(recipient.id),
            batch_id=batch_id,
            status="failed",
            error=str(error),
        )
    append_kp_layout_sent_mail_log(
        campaign=campaign,
        recipient=recipient,
        error=error,
        subject=subject,
        send_mode=send_mode,
        transport=str(campaign.transport or "smtp"),
    )
