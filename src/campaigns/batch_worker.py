"""Execute a campaign batch through the selected delivery connection."""

from __future__ import annotations

import mimetypes
import tempfile
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import format_datetime, make_msgid
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from src.campaigns.service import record_delivery_attempt
from src.campaigns.state import (
    CampaignStateConflict,
    recipient_metrics,
    terminal_status_for_metrics,
    transition_campaign_status,
)
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
    _assert_variables_filled(html, text)
    return html, text


def _assert_variables_filled(*values: str) -> None:
    from src.campaigns.substitution_engine import find_unresolved_placeholders

    unresolved: list[str] = []
    for value in values:
        for token in find_unresolved_placeholders(value):
            if token not in unresolved:
                unresolved.append(token)
    if unresolved:
        raise ValueError("Не заполнены переменные: " + ", ".join(unresolved))


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

    from src.generator.delivery.imap_sent import archive_sent_copy
    from src.generator.delivery.smtp_mailboxes import _open_smtp_connection, resolve_smtp_credentials

    creds = resolve_smtp_credentials(mailbox_id=mailbox_id, owner_username=owner_username)
    if sender_name and sender_name != creds.sender_name:
        creds = replace(creds, sender_name=sender_name)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{creds.sender_name} <{creds.email}>" if creds.sender_name else creds.email
    msg["To"] = to_email
    msg["Date"] = format_datetime(_now())
    message_id = make_msgid(domain=creds.email.rpartition("@")[2] or None)
    msg["Message-ID"] = message_id
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for filename, data in attachments or []:
        content_type, _ = mimetypes.guess_type(filename)
        maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

    raw_message = msg.as_bytes(policy=SMTP_POLICY)
    server: Any = _open_smtp_connection(creds)
    try:
        server.sendmail(creds.email, [to_email], raw_message)
    finally:
        try:
            server.quit()
        except Exception:
            pass

    try:
        archive_sent_copy(
            mailbox_id=mailbox_id,
            owner_username=owner_username,
            recipient=to_email,
            raw_message=raw_message,
            message_id=message_id,
        )
    except Exception:
        logger.exception(
            "SMTP message was accepted, but its IMAP sent copy could not be archived",
            mailbox_id=mailbox_id,
            message_id=message_id,
        )
    return message_id


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
    track_links: bool | None = None,
) -> str:
    """Send one message using the saved per-user connection."""
    from src.campaigns.connection_service import resolve_connection
    from src.generator.delivery.channel_guard import wait_for_channel_send_slot

    wait_for_channel_send_slot(
        connection_id,
        allow_warmup=send_mode == "connection_warmup",
    )
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
            try:
                message_id = _send_smtp_message(
                    mailbox_id=connection.id,
                    owner_username=owner_username,
                    to_email=to_email,
                    subject=subject,
                    html=html,
                    text=text,
                    sender_name=connection.sender_name,
                    attachments=attachments,
                )
            except Exception as exc:
                _record_submit_error(connection.id, to_email, exc)
                raise
            _record_smtp_accept(connection.id, message_id, to_email)
            return message_id

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
                credential_sending_key_id=connection.sending_key_id,
                credential_sender_name=connection.sender_name,
                credential_api_base_url=connection.api_base_url,
                track_links=track_links,
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
        from src.generator.delivery.provider_ids import normalize_provider_message_id

        message_id = normalize_provider_message_id(result.get("message_id") or result.get("uuid") or "")
        if not message_id:
            raise RuntimeError(f"{connection.transport} не вернул идентификатор письма.")
        # Store bare provider id so webhook task_id / message_id matches join keys.
        return message_id
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _record_submit_error(connection_id: str, recipient: str, error: Exception) -> None:
    try:
        from src.generator.delivery.channel_guard import record_channel_outcome
        from src.generator.delivery.suppression_store import upsert_from_provider_event

        error_text = str(error)
        record_channel_outcome(
            connection_id=connection_id,
            provider_message_id=f"submit-error:{uuid4()}",
            provider_status="error",
            recipient=recipient,
            smtp_response=error_text,
        )
        upsert_from_provider_event(
            recipient=recipient,
            provider_status="error",
            source="smtp_submit",
            delivery_response=error_text,
        )
    except Exception:
        logger.exception("delivery_channel_submit_error_record_failed", connection_id=connection_id)


def _record_smtp_accept(connection_id: str, message_id: str, recipient: str) -> None:
    try:
        from src.generator.delivery.channel_guard import record_channel_outcome

        record_channel_outcome(
            connection_id=connection_id,
            provider_message_id=message_id,
            provider_status="accepted",
            recipient=recipient,
        )
    except Exception:
        logger.exception("delivery_channel_smtp_accept_record_failed", connection_id=connection_id)


def run_sender_batch(kwargs: dict[str, Any]) -> dict[str, Any]:
    campaign_id = str(kwargs.get("campaign_id") or "")
    batch_id = str(kwargs.get("batch_id") or "")
    pause_ms = int(kwargs.get("pause_between_messages_ms") or 0)
    on_error = str(kwargs.get("on_error") or "skip")

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id, with_for_update=True)
        batch = session.get(CampaignBatch, batch_id, with_for_update=True)
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
        if camp.status in {"completed", "completed_with_errors"}:
            batch.status = "cancelled"
            batch.error = "Campaign is already completed"
            batch.completed_at = _now()
            session.flush()
            return {"status": "cancelled", "sent": 0}

        batch.status = "running"
        batch.started_at = _now()
        if camp.status == "scheduled":
            transition_campaign_status(
                session,
                camp,
                "running",
                reason="first_batch_started",
                actor="sender_worker",
            )
        elif camp.status != "running":
            raise CampaignStateConflict(
                f"Sender batch cannot start while campaign is {camp.status}"
            )
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
    from src.generator.delivery.email_validation import EmailValidationResult

    email_validation_cache: dict[str, EmailValidationResult] = {}
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
                    if on_error == "retry":
                        session.flush()
                        raise
                session.flush()
                if pause_ms > 0:
                    time.sleep(pause_ms / 1000.0)
                continue

            from src.campaigns.recipient_email_service import (
                persist_delivery_email_state,
                resolve_delivery_email,
                validation_attempts_error,
            )

            delivery_email, validation_attempts = resolve_delivery_email(
                recipient,
                validation_cache=email_validation_cache,
            )
            if not delivery_email:
                recipient.send_status = "failed"
                recipient.last_error = validation_attempts_error(validation_attempts)
                errors += 1
                batch.error_count = int(batch.error_count or 0) + 1
                camp.error_count = int(camp.error_count or 0) + 1
                record_delivery_attempt(
                    campaign_id=campaign_id,
                    recipient_id=int(recipient_id),
                    batch_id=batch_id,
                    status="failed",
                    error=recipient.last_error,
                )
                session.flush()
                continue

            persist_delivery_email_state(recipient, delivery_email)

            accepted = record_delivery_attempt(
                campaign_id=campaign_id,
                recipient_id=int(recipient_id),
                batch_id=batch_id,
                status="sending",
                delivery_email=delivery_email,
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
            _assert_variables_filled(subject)
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
                                "Email": delivery_email,
                            },
                            recipient=delivery_email,
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
                    to_email=delivery_email,
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
                    delivery_email=delivery_email,
                )
                hour_counts[connection_id] = hour_counts.get(connection_id, 0) + 1
                day_counts[connection_id] = day_counts.get(connection_id, 0) + 1
                sent += 1

                from src.campaigns.recipient_email_service import append_campaign_sent_mail_log
                from src.generator.delivery.manager_stats import invalidate_stats_cache

                if append_campaign_sent_mail_log(
                    job_id=job_id,
                    campaign_id=campaign_id,
                    recipient_id=int(recipient_id),
                    recipient=recipient,
                    delivery_email=delivery_email,
                    provider_message_id=message_id,
                    transport=transport,
                    send_mode=send_mode or "email",
                    subject=subject,
                    campaign_name=camp.name,
                    sent_at=_now().isoformat(),
                    connection_id=connection_id,
                ):
                    invalidate_stats_cache(job_id)
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
                if on_error == "retry":
                    session.flush()
                    raise
            session.flush()

        if pause_ms > 0:
            time.sleep(pause_ms / 1000.0)

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id, with_for_update=True)
        batch = session.get(CampaignBatch, batch_id, with_for_update=True)
        if batch:
            recipient_statuses = session.scalars(
                select(CampaignRecipient.send_status).where(
                    CampaignRecipient.id.in_(list(batch.recipient_ids or []))
                )
            ).all()
            has_final_problems = any(
                str(status or "") in {"skipped", "failed"}
                for status in recipient_statuses
            )
            batch.status = "completed_with_errors" if has_final_problems else "completed"
            batch.completed_at = _now()
        if camp:
            pending_batches = session.scalars(
                select(CampaignBatch).where(
                    CampaignBatch.campaign_id == campaign_id,
                    CampaignBatch.status.in_(["pending", "running", "paused"]),
                )
            ).all()
            if not pending_batches:
                metrics = recipient_metrics(session, camp)
                camp.sent_count = int(metrics["success_count"])
                target_status = terminal_status_for_metrics(metrics)
                if target_status and camp.status == "running":
                    transition_campaign_status(
                        session,
                        camp,
                        target_status,
                        reason="all_batches_finished",
                        actor="sender_worker",
                    )
            camp.updated_at = _now()
        session.flush()

    return {"status": "completed", "sent": sent, "errors": errors}


def finalize_sender_batch_task_failure(task_id: str, message: str) -> None:
    """Recover or finalize a sender batch when its queue task dies permanently."""
    from datetime import timedelta

    from src.campaigns.service import (
        MAX_SENDER_BATCH_WORKER_RECOVERIES,
        SENDER_BATCH_WORKER_RECOVERY_BACKOFF_SECONDS,
        enqueue_sender_batch_task,
    )
    from src.infra.models import BackgroundTask, CampaignSchedule

    with session_scope() as session:
        task = session.get(BackgroundTask, str(task_id))
        if task is None:
            return
        payload = dict(task.payload or {}) if isinstance(task.payload, dict) else {}
        batch_id = str(payload.get("batch_id") or "")
        campaign_id = str(payload.get("campaign_id") or "")
        if not batch_id or not campaign_id:
            return
        batch = session.get(CampaignBatch, batch_id, with_for_update=True)
        camp = session.get(Campaign, campaign_id, with_for_update=True)
        if batch is None or camp is None:
            return
        if batch.status not in {"running", "pending"}:
            return

        safe_message = str(message or "sender batch task failed").strip() or "sender batch task failed"
        now = _now()

        def _finalize_campaign_status() -> None:
            pending_batches = session.scalars(
                select(CampaignBatch).where(
                    CampaignBatch.campaign_id == campaign_id,
                    CampaignBatch.status.in_(["pending", "running", "paused"]),
                )
            ).all()
            if pending_batches:
                if camp.status == "scheduled":
                    transition_campaign_status(
                        session,
                        camp,
                        "running",
                        reason="batch_recovery_pending",
                        actor="sender_worker",
                    )
                return
            metrics = recipient_metrics(session, camp)
            camp.sent_count = int(metrics["success_count"])
            target_status = terminal_status_for_metrics(metrics)
            if target_status and camp.status == "scheduled":
                transition_campaign_status(
                    session,
                    camp,
                    "running",
                    reason="batch_failure_finalization",
                    actor="sender_worker",
                    at=now,
                )
            if target_status and camp.status == "running":
                transition_campaign_status(
                    session,
                    camp,
                    target_status,
                    reason="batch_failure_finalization",
                    actor="sender_worker",
                    at=now,
                )

        def _mark_unprocessed_batch_recipients_failed() -> int:
            recipient_ids = [int(value) for value in list(batch.recipient_ids or [])]
            if not recipient_ids:
                return 0
            recipients = session.scalars(
                select(CampaignRecipient).where(
                    CampaignRecipient.id.in_(recipient_ids),
                    CampaignRecipient.send_status == "pending",
                )
            ).all()
            for recipient in recipients:
                recipient.send_status = "failed"
                recipient.last_error = safe_message
            return len(recipients)

        if camp.status == "cancelled":
            batch.status = "failed"
            batch.error = safe_message[:2000]
            batch.completed_at = now
            camp.updated_at = now
            session.flush()
            return

        if int(batch.worker_recovery_count or 0) >= MAX_SENDER_BATCH_WORKER_RECOVERIES:
            batch.status = "failed"
            batch.error = safe_message[:2000]
            batch.completed_at = now
            _mark_unprocessed_batch_recipients_failed()
            logger.warning(
                "campaign_batch_recovery_exhausted",
                campaign_id=campaign_id,
                batch_id=batch_id,
                recoveries=batch.worker_recovery_count,
            )
            _finalize_campaign_status()
            camp.updated_at = now
            session.flush()
            return

        batch.worker_recovery_count = int(batch.worker_recovery_count or 0) + 1
        batch.status = "pending"
        batch.started_at = None
        batch.completed_at = None
        batch.error = safe_message[:2000]
        batch.scheduled_at = max(batch.scheduled_at, now)

        if camp.status == "paused":
            camp.updated_at = now
            session.flush()
            return

        schedule = session.scalar(
            select(CampaignSchedule).where(CampaignSchedule.campaign_id == campaign_id)
        )
        if schedule is None:
            batch.status = "failed"
            batch.completed_at = now
            batch.error = "Campaign schedule not found"
            _mark_unprocessed_batch_recipients_failed()
            _finalize_campaign_status()
            camp.updated_at = now
            session.flush()
            return

        available_at = now + timedelta(seconds=SENDER_BATCH_WORKER_RECOVERY_BACKOFF_SECONDS)
        enqueue_sender_batch_task(
            session,
            campaign_id=campaign_id,
            camp=camp,
            batch=batch,
            schedule=schedule,
            owner_username=camp.owner_username,
            available_at=available_at,
            idempotency_suffix=f"recovery:{int(now.timestamp())}:{batch.worker_recovery_count}",
        )
        if camp.status == "scheduled":
            transition_campaign_status(
                session,
                camp,
                "running",
                reason="batch_recovery_scheduled",
                actor="sender_worker",
                at=now,
            )
        camp.updated_at = now
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
