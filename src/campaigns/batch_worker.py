"""Execute a campaign batch through the selected delivery connection."""

from __future__ import annotations

import mimetypes
import smtplib
import tempfile
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from src.campaigns.service import record_delivery_attempt
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignBatch, CampaignRecipient, MailTemplate, TemplateVersion
from src.utils.logger import logger


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _render_body(
    template_html: str,
    recipient: CampaignRecipient,
    campaign: Campaign,
    template_text: str = "",
    *,
    email_template_id: str | None = None,
) -> tuple[str, str]:
    from src.campaigns.template_render_service import render_email_template_text

    html = template_html or (
        f"<p>Здравствуйте, {recipient.contact_name or 'коллеги'}!</p>"
        f"<p>{campaign.description or campaign.name}</p>"
    )
    html = render_email_template_text(
        html,
        recipient=recipient,
        campaign=campaign,
        template_id=email_template_id or campaign.email_template_id,
    )
    if template_text.strip():
        text = render_email_template_text(
            template_text,
            recipient=recipient,
            campaign=campaign,
            template_id=email_template_id or campaign.email_template_id,
        )
    else:
        text = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<p>", "").replace("</p>", "\n")
    return html, text


def _load_email_template(campaign: Campaign) -> tuple[str, str, str]:
    subject = campaign.mail_subject or campaign.name
    body_html = str((campaign.draft_payload or {}).get("email_body") or "")
    body_text = str((campaign.draft_payload or {}).get("email_body_text") or "")
    if campaign.email_template_id:
        with session_scope() as session:
            tmpl = session.get(MailTemplate, campaign.email_template_id)
            if tmpl and tmpl.active_version_id:
                version = session.get(TemplateVersion, tmpl.active_version_id)
                if version:
                    subject = version.subject or subject
                    body_html = version.body_html or body_html
                    body_text = version.body_text or body_text
    return subject, body_html, body_text


def _send_smtp_message(
    *,
    mailbox_id: str,
    owner_username: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
    sender_name: str = "",
    attachments: list[tuple[str, bytes]] | None = None,
) -> str:
    from dataclasses import replace

    from src.generator.delivery.smtp_mailboxes import resolve_smtp_credentials

    creds = resolve_smtp_credentials(mailbox_id=mailbox_id, owner_username=owner_username)
    if sender_name and sender_name != creds.sender_name:
        creds = replace(creds, sender_name=sender_name)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{creds.sender_name} <{creds.email}>" if creds.sender_name else creds.email
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for filename, data in attachments or []:
        content_type, _ = mimetypes.guess_type(filename)
        maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

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


def _send_delivery_message(
    *,
    connection_id: str,
    owner_username: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
    job_id: str | None = None,
    row_id: str = "",
    attachments: list[tuple[str, bytes]] | None = None,
    send_mode: str | None = None,
    send_run_id: str | None = None,
    campaign: Campaign | None = None,
) -> str:
    """Send one message using the saved per-user connection."""
    from src.campaigns.connection_service import resolve_connection

    connection = resolve_connection(connection_id, owner_username, campaign=campaign)
    attachment_paths: list[str] = []
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if attachments:
        import tempfile

        temp_dir = tempfile.TemporaryDirectory(prefix="campaign-chain-")
        for filename, data in attachments:
            path = Path(temp_dir.name) / filename
            path.write_bytes(data)
            attachment_paths.append(str(path))

    try:
        if connection.transport == "smtp":
            return _send_smtp_message(
                mailbox_id=connection.id,
                owner_username=owner_username,
                to_email=to_email,
                subject=subject,
                html=html,
                text=text,
                sender_name=connection.sender_name,
                attachments=attachments,
            )

        row = {"ID": row_id or f"test-{int(_now().timestamp())}", "EMAIL": to_email}
        if connection.transport == "rusender":
            from src.generator.delivery.sender_agent import _send_via_rusender

            result = _send_via_rusender(
                row,
                to_email,
                attachment_paths,
                subject,
                body_override=text,
                html_override=html,
                job_id=job_id,
                send_run_id=send_run_id or "",
                send_mode=send_mode or "",
                sender_email=connection.email,
                credential_api_key=connection.secret,
                credential_sender_name=connection.sender_name,
                credential_api_base_url=connection.api_base_url,
            )
        elif connection.transport == "mailopost":
            from src.generator.delivery.sender_agent import _send_via_mailopost

            result = _send_via_mailopost(
                row,
                to_email,
                attachment_paths,
                subject,
                body_override=text,
                html_override=html,
                job_id=job_id,
                send_run_id=send_run_id or "",
                send_mode=send_mode or "",
                sender_email=connection.email,
                credential_api_token=connection.secret,
                credential_sender_name=connection.sender_name,
                credential_api_base_url=connection.api_base_url,
            )
        else:
            raise RuntimeError(f"Неподдерживаемый способ отправки: {connection.transport}")
        message_id = str(result.get("message_id") or result.get("uuid") or "")
        if not message_id:
            raise RuntimeError(f"{connection.transport} не вернул идентификатор письма.")
        return f"{connection.transport}:{message_id}"
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def run_sender_batch(kwargs: dict[str, Any]) -> dict[str, Any]:
    campaign_id = str(kwargs.get("campaign_id") or "")
    batch_id = str(kwargs.get("batch_id") or "")
    pause_ms = int(kwargs.get("pause_between_messages_ms") or 0)
    on_error = str(kwargs.get("on_error") or "skip")

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
        owner = camp.owner_username
        subject_template, body_html_template, body_text_template = _load_email_template(camp)
        job_id = camp.job_id
        from src.campaigns.connection_service import campaign_connection_ids, normalize_connection_ids

        raw_connection_ids = kwargs.get("connection_ids")
        if raw_connection_ids:
            connection_ids = normalize_connection_ids(list(raw_connection_ids))
        else:
            connection_ids = campaign_connection_ids(camp)

    if not connection_ids:
        raise RuntimeError("Не выбрано подключение отправителя.")

    send_mode = str(kwargs.get("send_mode") or "")
    if send_mode == "materials" and job_id:
        from src.campaigns.generation_service import ensure_campaign_workspace

        ensure_campaign_workspace(campaign_id, owner)

    sent = 0
    errors = 0
    hour_counts: dict[str, int] = {}
    day_counts: dict[str, int] = {}
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
            if recipient.send_status in {"sent", "in_chain"}:
                continue

            from src.generator.delivery.suppression_store import is_suppressed

            suppressed, suppress_reason = is_suppressed(recipient.email)
            if suppressed:
                recipient.excluded = True
                recipient.send_status = "skipped"
                recipient.last_error = f"Адрес в стоп-листе ({suppress_reason or 'suppressed'})"
                session.flush()
                continue

            send_mode = str(kwargs.get("send_mode") or "")
            if send_mode == "chain_root":
                from src.campaigns.chain_send_service import send_chain_node_email
                from src.campaigns.chain_service import get_email_chain

                chain = get_email_chain(camp)
                root_id = str(chain.get("root_node_id") or "")
                try:
                    result = send_chain_node_email(
                        campaign_id=campaign_id,
                        recipient_id=int(recipient_id),
                        node_id=root_id,
                        batch_id=batch_id,
                        hour_counts=hour_counts,
                        day_counts=day_counts,
                    )
                    if result.get("status") == "skipped":
                        recipient.send_status = "skipped"
                        session.flush()
                        continue
                    batch.sent_count = int(batch.sent_count or 0) + 1
                    camp.sent_count = int(camp.sent_count or 0) + 1
                    sent += 1
                except Exception as exc:
                    errors += 1
                    recipient.send_status = "failed"
                    recipient.last_error = str(exc)
                    batch.error_count = int(batch.error_count or 0) + 1
                    camp.error_count = int(camp.error_count or 0) + 1
                    logger.exception("campaign_chain_root_failed", campaign_id=campaign_id, recipient_id=recipient_id)
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
                continue

            accepted = record_delivery_attempt(
                campaign_id=campaign_id, recipient_id=int(recipient_id), batch_id=batch_id, status="sending"
            )
            if not accepted and recipient.send_status == "sent":
                continue

            html, text = _render_body(
                body_html_template,
                recipient,
                camp,
                body_text_template,
                email_template_id=camp.email_template_id,
            )
            from src.campaigns.chain_template_utils import strip_chain_button_placeholder
            from src.campaigns.template_render_service import render_email_template_text

            html = strip_chain_button_placeholder(html)
            subject = render_email_template_text(
                subject_template,
                recipient=recipient,
                campaign=camp,
                template_id=camp.email_template_id,
            )
            try:
                from src.campaigns.connection_service import pick_available_connection

                connection = pick_available_connection(
                    connection_ids,
                    owner,
                    hour_counts,
                    day_counts,
                    campaign=camp,
                )
                if connection is None:
                    raise RuntimeError("Все подключения исчерпали лимиты отправки")
                connection_id = connection.id
                transport = connection.transport

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
                            transport=transport,
                            attachment_mode=camp.document_mode or "kp",
                            subject_template=subject,
                            campaign_name=camp.name,
                            sender_email=connection.email,
                            connection_id=connection.id,
                            owner_username=owner,
                        )
                        token = str((consent or {}).get("token") or "")
                        link = str((consent or {}).get("consent_url") or "")
                        if not token or not link:
                            raise RuntimeError("Consent request was not persisted and has no public URL.")
                        html = f'{html}<p><a href="{link}">Подтвердить согласие</a></p>'
                        text = f"{text}\n\nПодтвердить согласие: {link}"
                    except Exception as consent_exc:
                        logger.warning("campaign_consent_prepare_failed", error=str(consent_exc))
                        raise RuntimeError(f"Consent request preparation failed: {consent_exc}") from consent_exc

                attachments: list[tuple[str, bytes]] = []
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
                        owner_username=owner,
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

                message_id = _send_delivery_message(
                    connection_id=connection_id,
                    owner_username=owner,
                    to_email=recipient.email,
                    subject=subject,
                    html=html,
                    text=text,
                    job_id=job_id,
                    row_id=str(recipient.id),
                    attachments=attachments,
                    campaign=camp,
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
                hour_counts[connection_id] = hour_counts.get(connection_id, 0) + 1
                day_counts[connection_id] = day_counts.get(connection_id, 0) + 1
                sent += 1

                if job_id:
                    try:
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
                                    (recipient.row_index + 1)
                                    if recipient.row_index is not None
                                    else recipient.id
                                ),
                                "status": "sent",
                                "transport": transport,
                                "campaign_name": camp.name,
                                "campaign_id": campaign_id,
                                "sent_at": _now().isoformat(),
                                "subject": subject,
                                "send_mode": "materials",
                                "provider_message_id": message_id,
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


def finalize_sender_batch_task_failure(task_id: str, message: str) -> None:
    """Mark a stuck sender batch failed when its queue task dies permanently."""
    from src.infra.models import BackgroundTask

    with session_scope() as session:
        task = session.get(BackgroundTask, str(task_id))
        if task is None:
            return
        payload = dict(task.payload or {}) if isinstance(task.payload, dict) else {}
        batch_id = str(payload.get("batch_id") or "")
        campaign_id = str(payload.get("campaign_id") or "")
        if not batch_id or not campaign_id:
            return
        batch = session.get(CampaignBatch, batch_id)
        camp = session.get(Campaign, campaign_id)
        if batch is None or camp is None:
            return
        if batch.status not in {"running", "pending"}:
            return

        safe_message = str(message or "sender batch task failed").strip() or "sender batch task failed"
        batch.status = "failed"
        batch.error = safe_message[:2000]
        batch.completed_at = _now()

        active_batches = session.scalars(
            select(CampaignBatch).where(
                CampaignBatch.campaign_id == campaign_id,
                CampaignBatch.status.in_(["pending", "running"]),
                CampaignBatch.id != batch_id,
            )
        ).all()
        if active_batches:
            camp.updated_at = _now()
            session.flush()
            return

        pending_recipients = session.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.excluded.is_(False),
                CampaignRecipient.send_status == "pending",
            )
        ) or 0

        if int(pending_recipients) > 0:
            camp.status = "paused"
        elif int(camp.error_count or 0) > 0:
            camp.status = "completed_with_errors"
            camp.completed_at = _now()
        elif int(camp.sent_count or 0) >= int(camp.total_count or 0) and int(camp.total_count or 0) > 0:
            camp.status = "completed"
            camp.completed_at = _now()
        else:
            camp.status = "paused"
        camp.updated_at = _now()
        session.flush()


def run_campaign_pre_generate(kwargs: dict[str, Any]) -> dict[str, Any]:
    from src.campaigns.template_render_service import pre_generate_batch_templates

    campaign_id = str(kwargs.get("campaign_id") or "")
    recipient_ids = [int(item) for item in (kwargs.get("recipient_ids") or [])]
    result = pre_generate_batch_templates(
        campaign_id=campaign_id,
        recipient_ids=recipient_ids,
    )
    return {"status": "ok", **result}
