"""Retry campaign sends on the next validated email after delivery failure."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from src.campaigns.recipient_email_service import (
    RECIPIENT_STRATEGY_PRIMARY_THEN_FALLBACK,
    append_campaign_sent_mail_log,
    persist_delivery_email_state,
    resolve_delivery_email,
)
from src.campaigns.service import record_delivery_attempt
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient, DeliveryAttempt
from src.utils.logger import logger

_CAMPAIGN_FALLBACK_LOCK = threading.Lock()
_CAMPAIGN_FALLBACK_RUNNING: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _campaign_sent_items(job_id: str, *, campaign_id: str | None = None) -> list[dict[str, Any]]:
    from src.jobs.job_docs import read_sent_mail_log

    items = read_sent_mail_log(job_id)
    result: list[dict[str, Any]] = []
    for item in items:
        if not str(item.get("campaign_id") or "").strip():
            continue
        if campaign_id and str(item.get("campaign_id") or "") != campaign_id:
            continue
        if str(item.get("recipient_strategy") or "") != RECIPIENT_STRATEGY_PRIMARY_THEN_FALLBACK:
            continue
        result.append(item)
    return result


def _collect_campaign_ids_from_sent_log(job_id: str) -> set[str]:
    return {
        str(item.get("campaign_id") or "").strip()
        for item in _campaign_sent_items(job_id)
        if str(item.get("campaign_id") or "").strip()
    }


def resend_campaign_recipient_email(
    *,
    campaign_id: str,
    recipient_id: int,
    delivery_email: str,
    send_mode: str,
    subject: str,
    transport: str,
    connection_id: str,
    owner_username: str,
    job_id: str | None,
    send_run_id: str = "",
    attempt_number: int | None = None,
) -> str:
    from src.campaigns.batch_worker import _load_email_template, _render_body, _send_delivery_message
    from src.campaigns.chain_template_utils import strip_chain_button_placeholder
    from src.campaigns.template_render_service import render_email_template_text

    rendered_subject = subject
    html = ""
    text = ""
    attachments: list[tuple[str, bytes]] = []
    campaign_for_send: Campaign | None = None
    consent_token = ""
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        recipient = session.get(CampaignRecipient, int(recipient_id))
        if camp is None or recipient is None:
            raise ValueError("campaign or recipient not found")

        subject_template, body_html_template, body_text_template = _load_email_template(camp)
        html, text = _render_body(
            body_html_template,
            recipient,
            camp,
            body_text_template,
            email_template_id=camp.email_template_id,
        )
        html = strip_chain_button_placeholder(html)
        rendered_subject = render_email_template_text(
            subject or subject_template,
            recipient=recipient,
            campaign=camp,
            template_id=camp.email_template_id,
        )

        if attempt_number is None:
            latest_attempt = session.scalar(
                select(func.max(DeliveryAttempt.attempt_number)).where(
                    DeliveryAttempt.campaign_id == campaign_id,
                    DeliveryAttempt.recipient_id == int(recipient_id),
                )
            )
            attempt_number = int(latest_attempt or 0) + 1
        record_delivery_attempt(
            campaign_id=campaign_id,
            recipient_id=int(recipient_id),
            batch_id=None,
            status="sending",
            delivery_email=delivery_email,
            attempt_number=attempt_number,
        )
        persist_delivery_email_state(recipient, delivery_email)

        if send_mode == "consent_request" and job_id:
            from src.campaigns.connection_service import resolve_connection
            from src.generator.delivery.consent_store import prepare_consent_request

            connection = resolve_connection(connection_id, owner_username, campaign=camp)
            consent = prepare_consent_request(
                job_id=job_id,
                row={
                    "ID": str(recipient.id),
                    "Организация": recipient.company,
                    "Контакт": recipient.contact_name,
                    "Email": delivery_email,
                },
                recipient=delivery_email,
                transport=transport,
                attachment_mode=camp.document_mode or "kp",
                subject_template=rendered_subject,
                campaign_name=camp.name,
                sender_email=connection.email,
                connection_id=connection.id,
                owner_username=owner_username,
            )
            consent_token = str((consent or {}).get("token") or "")
            consent_link = str((consent or {}).get("consent_url") or "")
            if not consent_token or not consent_link:
                raise RuntimeError("Consent request was not persisted and has no public URL.")
            html = f'{html}<p><a href="{consent_link}">Подтвердить согласие</a></p>'
            text = f"{text}\n\nПодтвердить согласие: {consent_link}"

        if send_mode == "materials" and job_id:
            from src.campaigns.generation_service import ensure_recipient_documents
            from src.generator.delivery.sender_agent import (
                _resolve_output_folder,
                _resolve_pdf_attachments,
            )
            from src.jobs.storage import resolve_job_paths

            ensure_recipient_documents(
                campaign_id=campaign_id,
                recipient_id=int(recipient.id),
                owner_username=owner_username,
                job_id=job_id,
                document_mode=camp.document_mode or "kp",
                work_type=camp.work_type,
            )
            folder, folder_error = _resolve_output_folder(
                recipient.id,
                output_dir=resolve_job_paths(job_id).output_dir,
            )
            if folder_error:
                raise RuntimeError(folder_error)
            attachment_paths, attachment_error = _resolve_pdf_attachments(
                folder,
                attachment_mode=camp.document_mode or "kp",
            )
            if attachment_error:
                raise RuntimeError(attachment_error)
            attachments = [
                (Path(raw_path).name, Path(raw_path).read_bytes())
                for raw_path in attachment_paths
            ]

        session.flush()
        session.expunge(camp)
        campaign_for_send = camp

    try:
        message_id = _send_delivery_message(
            connection_id=connection_id,
            owner_username=owner_username,
            to_email=delivery_email,
            subject=rendered_subject,
            html=html,
            text=text,
            job_id=job_id,
            row_id=str(recipient_id),
            attachments=attachments,
            send_mode=send_mode,
            send_run_id=send_run_id,
            campaign=campaign_for_send,
        )
    except Exception as exc:
        record_delivery_attempt(
            campaign_id=campaign_id,
            recipient_id=int(recipient_id),
            batch_id=None,
            status="failed",
            error=str(exc),
            delivery_email=delivery_email,
            attempt_number=attempt_number,
        )
        with session_scope() as session:
            recipient = session.get(CampaignRecipient, int(recipient_id))
            if recipient is not None:
                recipient.send_status = "failed"
                recipient.last_error = str(exc)
        raise

    with session_scope() as session:
        recipient = session.get(CampaignRecipient, int(recipient_id))
        camp = session.get(Campaign, campaign_id)
        if recipient is not None:
            recipient.send_status = "sent"
            recipient.last_error = None
            session.flush()
        if recipient is not None and camp is not None:
            append_campaign_sent_mail_log(
                job_id=job_id,
                campaign_id=campaign_id,
                recipient_id=int(recipient_id),
                recipient=recipient,
                delivery_email=delivery_email,
                provider_message_id=message_id,
                transport=transport,
                send_mode=send_mode,
                subject=rendered_subject,
                campaign_name=camp.name,
                sent_at=_now().isoformat(),
                connection_id=connection_id,
            )

    record_delivery_attempt(
        campaign_id=campaign_id,
        recipient_id=int(recipient_id),
        batch_id=None,
        status="sent",
        provider_message_id=message_id,
        delivery_email=delivery_email,
        attempt_number=attempt_number,
    )
    if consent_token and job_id:
        from src.generator.delivery.consent_store import mark_consent_request_sent

        mark_consent_request_sent(
            job_id=job_id,
            row_id=str(recipient_id),
            recipient=delivery_email,
            provider={
                "message_id": message_id,
                "transport": transport,
                "connection_id": connection_id,
            },
            attachment_mode=campaign_for_send.document_mode if campaign_for_send else None,
        )
    return message_id

def process_campaign_delivery_fallbacks(
    *,
    job_id: str,
    provider: str = "",
    campaign_id: str | None = None,
) -> dict[str, Any]:
    from src.generator.delivery.sender_agent import (
        _is_delivery_failure_status,
        _latest_delivery_events_by_row_recipient,
        _mail_key,
        _safe_text,
        _sent_item_order_key,
        _sent_items_by_row,
    )

    sent_items = _campaign_sent_items(job_id, campaign_id=campaign_id)
    if not sent_items:
        return {"status": "no_campaign_sent_log", "job_id": job_id, "dispatched_rows": []}

    latest_events = _latest_delivery_events_by_row_recipient(job_id, sent_items, provider=provider)
    sent_by_row = _sent_items_by_row(sent_items)
    dispatched_rows: list[dict[str, Any]] = []

    for row_id, row_items in sent_by_row.items():
        last_item = max(row_items, key=_sent_item_order_key)
        failed_recipient_key = _mail_key(last_item.get("recipient"))
        if not failed_recipient_key:
            continue
        event = latest_events.get((row_id, failed_recipient_key))
        if not event or not _is_delivery_failure_status(event.get("provider_status") or event.get("event_type")):
            continue

        item_campaign_id = _safe_text(last_item.get("campaign_id"))
        recipient_id = int(last_item.get("recipient_id") or row_id or 0)
        if not item_campaign_id or not recipient_id:
            continue

        logged_recipients = {_mail_key(item.get("recipient")) for item in row_items if _mail_key(item.get("recipient"))}
        delivery_email: str | None = None
        with session_scope() as session:
            recipient = session.get(CampaignRecipient, int(recipient_id))
            if recipient is None:
                continue
            tried = list((recipient.extra or {}).get("tried_emails") or [])
            tried.extend([_safe_text(item.get("recipient")) for item in row_items if _safe_text(item.get("recipient"))])
            delivery_email, _attempts = resolve_delivery_email(recipient, skip_emails=tried)
            if delivery_email and _mail_key(delivery_email) in logged_recipients:
                delivery_email = None

        if not delivery_email:
            continue

        owner_username = ""
        connection_id = ""
        with session_scope() as session:
            camp = session.get(Campaign, item_campaign_id)
            recipient = session.get(CampaignRecipient, int(recipient_id))
            if camp is None or recipient is None:
                continue
            owner_username = camp.owner_username
            from src.campaigns.connection_service import campaign_connection_ids, pick_available_connection

            connection = pick_available_connection(campaign_connection_ids(camp), owner_username, {}, {}, campaign=camp)
            if connection is None:
                continue
            connection_id = connection.id
            transport = connection.transport
            subject = _safe_text(last_item.get("subject")) or camp.mail_subject or camp.name
            send_mode = _safe_text(last_item.get("send_mode")) or "email"
            job_ref = camp.job_id

        try:
            message_id = resend_campaign_recipient_email(
                campaign_id=item_campaign_id,
                recipient_id=int(recipient_id),
                delivery_email=delivery_email,
                send_mode=send_mode,
                subject=subject,
                transport=transport,
                connection_id=connection_id,
                owner_username=owner_username,
                job_id=job_ref,
            )
        except Exception as exc:
            logger.exception(
                "campaign_delivery_fallback_resend_failed",
                campaign_id=item_campaign_id,
                recipient_id=recipient_id,
            )
            record_delivery_attempt(
                campaign_id=item_campaign_id,
                recipient_id=int(recipient_id),
                batch_id=None,
                status="failed",
                error=str(exc),
                delivery_email=delivery_email,
            )
            continue

        dispatched_rows.append(
            {
                "campaign_id": item_campaign_id,
                "recipient_id": str(recipient_id),
                "failed_recipient": _safe_text(last_item.get("recipient")),
                "next_recipient": delivery_email,
                "provider_status": _safe_text(event.get("provider_status") or event.get("event_type")),
                "message_id": message_id,
            }
        )

    if not dispatched_rows:
        return {"status": "no_fallback_needed", "job_id": job_id, "dispatched_rows": []}
    return {"status": "ok", "job_id": job_id, "dispatched_rows": dispatched_rows}


def schedule_campaign_delivery_fallback_check(job_ids: Any, *, provider: str = "") -> None:
    if isinstance(job_ids, str):
        normalized_job_ids = [job_ids]
    else:
        try:
            normalized_job_ids = list(job_ids or [])
        except TypeError:
            normalized_job_ids = []
    provider_key = str(provider or "").strip().lower() or "provider"
    for raw_job_id in normalized_job_ids:
        job_id = str(raw_job_id or "").strip()
        if not job_id:
            continue
        key = f"{provider_key}:{job_id}"
        with _CAMPAIGN_FALLBACK_LOCK:
            if key in _CAMPAIGN_FALLBACK_RUNNING:
                continue
            _CAMPAIGN_FALLBACK_RUNNING.add(key)
        thread = threading.Thread(
            target=_run_scheduled_campaign_delivery_fallback_check,
            args=(job_id, provider_key, key),
            name=f"campaign-fallback-{job_id[:12]}",
            daemon=True,
        )
        thread.start()


def _run_scheduled_campaign_delivery_fallback_check(job_id: str, provider: str, running_key: str) -> None:
    try:
        campaign_ids = _collect_campaign_ids_from_sent_log(job_id)
        for campaign_id in sorted(campaign_ids):
            process_campaign_delivery_fallbacks(job_id=job_id, provider=provider, campaign_id=campaign_id)
    finally:
        with _CAMPAIGN_FALLBACK_LOCK:
            _CAMPAIGN_FALLBACK_RUNNING.discard(running_key)


def schedule_campaign_delivery_fallbacks_from_webhook_result(result: Any, *, provider: str) -> None:
    if not isinstance(result, dict):
        return
    jobs = result.get("jobs")
    if not jobs:
        return
    schedule_campaign_delivery_fallback_check(jobs, provider=provider)
