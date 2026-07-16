"""Execute a campaign batch: send emails via SMTP mailbox with idempotency."""

from __future__ import annotations

import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select

from src.campaigns.service import record_delivery_attempt
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignBatch, CampaignRecipient, MailTemplate, TemplateVersion
from src.utils.logger import logger


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _render_body(template_html: str, recipient: CampaignRecipient, campaign: Campaign) -> tuple[str, str]:
    html = template_html or f"<p>Здравствуйте, {recipient.contact_name or 'коллеги'}!</p><p>{campaign.description or campaign.name}</p>"
    replacements = {
        "{{company}}": recipient.company,
        "{{contact_name}}": recipient.contact_name,
        "{{email}}": recipient.email,
        "{{campaign_name}}": campaign.name,
        "{{region}}": recipient.region,
    }
    for key, value in replacements.items():
        html = html.replace(key, value or "")
    text = (
        html.replace("<br>", "\n")
        .replace("<br/>", "\n")
        .replace("<p>", "")
        .replace("</p>", "\n")
    )
    return html, text


def _load_email_template(campaign: Campaign) -> tuple[str, str]:
    subject = campaign.mail_subject or campaign.name
    body_html = str((campaign.draft_payload or {}).get("email_body") or "")
    if campaign.email_template_id:
        with session_scope() as session:
            tmpl = session.get(MailTemplate, campaign.email_template_id)
            if tmpl and tmpl.active_version_id:
                version = session.get(TemplateVersion, tmpl.active_version_id)
                if version:
                    subject = version.subject or subject
                    body_html = version.body_html or body_html
    return subject, body_html


def _send_smtp_message(
    *,
    mailbox_id: str,
    owner_username: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
) -> str:
    from src.generator.delivery.smtp_mailboxes import resolve_smtp_credentials

    creds = resolve_smtp_credentials(mailbox_id=mailbox_id, owner_username=owner_username)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{creds.sender_name} <{creds.email}>" if creds.sender_name else creds.email
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    if creds.use_ssl:
        server: Any = smtplib.SMTP_SSL(creds.host, creds.port, timeout=60)
    else:
        server = smtplib.SMTP(creds.host, creds.port, timeout=60)
        if creds.use_starttls:
            server.starttls()
    try:
        if creds.password:
            server.login(creds.smtp_username or creds.email, creds.password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return f"smtp:{to_email}:{int(_now().timestamp())}"


def run_sender_batch(kwargs: dict[str, Any]) -> dict[str, Any]:
    campaign_id = str(kwargs.get("campaign_id") or "")
    batch_id = str(kwargs.get("batch_id") or "")
    pause_ms = int(kwargs.get("pause_between_messages_ms") or 0)
    on_error = str(kwargs.get("on_error") or "retry")

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        batch = session.get(CampaignBatch, batch_id)
        if camp is None or batch is None:
            raise ValueError("campaign or batch not found")
        if camp.status == "paused":
            batch.status = "paused"
            session.flush()
            return {"status": "paused", "sent": 0}
        if camp.status == "cancelled" or batch.status == "cancelled":
            batch.status = "cancelled"
            session.flush()
            return {"status": "cancelled", "sent": 0}

        batch.status = "running"
        batch.started_at = _now()
        if camp.status == "scheduled":
            camp.status = "running"
        session.flush()

        recipient_ids = list(batch.recipient_ids or [])
        mailbox_id = str(kwargs.get("smtp_mailbox_id") or camp.smtp_mailbox_id or "")
        owner = camp.owner_username
        subject_template, body_html_template = _load_email_template(camp)
        job_id = camp.job_id

    sent = 0
    errors = 0
    for recipient_id in recipient_ids:
        with session_scope() as session:
            camp = session.get(Campaign, campaign_id)
            batch = session.get(CampaignBatch, batch_id)
            recipient = session.get(CampaignRecipient, int(recipient_id))
            if camp is None or batch is None or recipient is None:
                continue
            if camp.status == "paused":
                batch.status = "paused"
                session.flush()
                return {"status": "paused", "sent": sent, "errors": errors}
            if camp.status == "cancelled":
                batch.status = "cancelled"
                session.flush()
                return {"status": "cancelled", "sent": sent, "errors": errors}

            if recipient.send_status == "sent":
                continue

            accepted = record_delivery_attempt(
                campaign_id=campaign_id,
                recipient_id=int(recipient_id),
                batch_id=batch_id,
                status="sending",
            )
            if not accepted and recipient.send_status == "sent":
                continue

            html, text = _render_body(body_html_template, recipient, camp)
            subject = subject_template.replace("{{company}}", recipient.company or "")
            try:
                if not mailbox_id:
                    raise RuntimeError("SMTP mailbox is not configured")
                # Consent request path: create consent token when scenario requires it
                if str(kwargs.get("send_mode") or "") == "consent_request" and job_id:
                    try:
                        from src.generator.delivery.consent_store import prepare_consent_request

                        consent = prepare_consent_request(
                            job_id=job_id,
                            row={
                                "ID": str(recipient.id),
                                "Организация": recipient.company,
                                "Контакт": recipient.contact_name,
                                "Email": recipient.email,
                            },
                            recipient=recipient.email,
                            transport="smtp",
                            attachment_mode=camp.document_mode or "kp",
                            subject_template=subject,
                            campaign_name=camp.name,
                            sender_email=None,
                        )
                        token = str((consent or {}).get("token") or "")
                        if token:
                            from src.utils.config import settings

                            base = str(getattr(settings, "public_base_url", "") or "http://localhost:9806").rstrip("/")
                            link = f"{base}/consent/confirm/{token}"
                            html = f"{html}<p><a href=\"{link}\">Подтвердить согласие</a></p>"
                            text = f"{text}\n\nПодтвердить согласие: {link}"
                    except Exception as consent_exc:
                        logger.warning("campaign_consent_prepare_failed", error=str(consent_exc))

                message_id = _send_smtp_message(
                    mailbox_id=mailbox_id,
                    owner_username=owner,
                    to_email=recipient.email,
                    subject=subject,
                    html=html,
                    text=text,
                )
                recipient.send_status = "sent"
                recipient.last_error = None
                batch.sent_count = int(batch.sent_count or 0) + 1
                camp.sent_count = int(camp.sent_count or 0) + 1
                record_delivery_attempt(
                    campaign_id=campaign_id,
                    recipient_id=int(recipient_id),
                    batch_id=batch_id,
                    status="sent",
                    provider_message_id=message_id,
                )
                sent += 1

                if job_id:
                    try:
                        from src.jobs.job_docs import append_event

                        append_event(
                            job_id,
                            "sent_mail_log",
                            {
                                "email": recipient.email,
                                "organization": recipient.company,
                                "status": "sent",
                                "transport": "smtp",
                                "campaign_name": camp.name,
                                "campaign_id": campaign_id,
                                "sent_at": _now().isoformat(),
                                "subject": subject,
                            },
                        )
                    except Exception:
                        pass
            except Exception as exc:
                errors += 1
                recipient.send_status = "failed"
                recipient.last_error = str(exc)
                batch.error_count = int(batch.error_count or 0) + 1
                camp.error_count = int(camp.error_count or 0) + 1
                record_delivery_attempt(
                    campaign_id=campaign_id,
                    recipient_id=int(recipient_id),
                    batch_id=batch_id,
                    status="failed",
                    error=str(exc),
                )
                logger.exception("campaign_batch_send_failed", campaign_id=campaign_id, recipient_id=recipient_id)
                if on_error == "pause":
                    camp.status = "paused"
                    batch.status = "paused"
                    session.flush()
                    return {"status": "paused", "sent": sent, "errors": errors}
                if on_error == "retry":
                    session.flush()
                    raise
            session.flush()

        if pause_ms > 0:
            time.sleep(pause_ms / 1000.0)

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        batch = session.get(CampaignBatch, batch_id)
        if batch:
            batch.status = "completed" if errors == 0 else "completed_with_errors"
            batch.completed_at = _now()
        if camp:
            pending_batches = session.scalars(
                select(CampaignBatch).where(
                    CampaignBatch.campaign_id == campaign_id,
                    CampaignBatch.status.in_(["pending", "running", "paused"]),
                )
            ).all()
            if not pending_batches:
                camp.status = "completed" if camp.error_count == 0 else "completed_with_errors"
                camp.completed_at = _now()
            camp.updated_at = _now()
        session.flush()

    return {"status": "completed", "sent": sent, "errors": errors}
