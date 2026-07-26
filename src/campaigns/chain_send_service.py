"""Send emails for email-chain nodes with branch buttons and document attachments."""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import select

from src.campaigns.chain_service import (
    LINK_KIND_CUSTOM,
    create_branch_tokens,
    find_node,
    get_email_chain,
    is_email_node,
    is_link_node,
    mark_token_sent,
    outgoing_edges,
    resolve_button_label,
)
from src.campaigns.chain_template_utils import inject_chain_buttons
from src.campaigns.service import record_delivery_attempt
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignChainToken, CampaignRecipient, MailTemplate, TemplateVersion
from src.utils.config import settings
from src.utils.logger import logger

_CHAIN_FOLLOWUP_PRIORITY = 100


def _load_node_email_template(node: dict[str, Any], campaign: Campaign) -> tuple[str, str, str]:
    subject = campaign.mail_subject or campaign.name
    body_html = ""
    body_text = ""
    template_id = node.get("email_template_id")
    if template_id:
        with session_scope() as session:
            tmpl = session.get(MailTemplate, str(template_id))
            if tmpl and tmpl.active_version_id:
                version = session.get(TemplateVersion, tmpl.active_version_id)
                if version:
                    subject = version.subject or subject
                    body_html = version.body_html or body_html
                    body_text = version.body_text or body_text
    return subject, body_html, body_text


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
        template_id=email_template_id,
    )
    if template_text.strip():
        text = render_email_template_text(
            template_text,
            recipient=recipient,
            campaign=campaign,
            template_id=email_template_id,
        )
    else:
        text = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<p>", "").replace("</p>", "\n")
    return html, text


def _resolve_document_attachments(
    document_template_ids: list[str],
    *,
    campaign: Campaign,
    recipient: CampaignRecipient,
) -> list[tuple[str, bytes]]:
    from src.campaigns.template_render_service import resolve_cached_attachment

    attachments: list[tuple[str, bytes]] = []
    if not document_template_ids:
        return attachments
    owner = campaign.owner_username
    job_id = campaign.job_id
    for template_id in document_template_ids:
        resolved = resolve_cached_attachment(
            template_id=str(template_id),
            recipient_id=int(recipient.id),
            job_id=job_id,
            owner_username=owner,
            campaign=campaign,
            recipient=recipient,
        )
        if resolved:
            attachments.append(resolved)
    return attachments


def _persist_chain_branch_tokens(
    *,
    campaign_id: str,
    recipient_id: int,
    source_node_id: str,
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    test_email: str | None,
) -> list[tuple[str, str, str]]:
    """Commit branch tokens before sending so links work even if post-send steps fail."""
    if not edges:
        return []
    with session_scope() as session:
        token_rows = create_branch_tokens(
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            source_node_id=source_node_id,
            edges=edges,
            test_email=test_email,
        )
        for row in token_rows:
            session.add(row)
        session.flush()
        return [
            (
                resolve_button_label(edge, node_by_id),
                row.token,
                str((node_by_id.get(str(edge.get("target_id") or "")) or {}).get("link_kind") or LINK_KIND_CUSTOM),
            )
            for edge, row in zip(edges, token_rows, strict=True)
        ]


def _record_chain_send_failure(
    *,
    campaign_id: str,
    recipient_id: int,
    batch_id: str | None,
    followup_token: str | None,
    active_test_email: str | None,
    error: Exception,
) -> None:
    with session_scope() as session:
        if followup_token:
            mark_token_sent(followup_token, error=str(error))
        if batch_id and not active_test_email:
            record_delivery_attempt(
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                batch_id=batch_id,
                status="failed",
                error=str(error),
            )
        if not active_test_email:
            recipient = session.get(CampaignRecipient, int(recipient_id))
            if recipient is not None:
                recipient.last_error = str(error)
                session.flush()


def _finalize_chain_send_success(
    *,
    campaign_id: str,
    recipient_id: int,
    node_id: str,
    root_node_id: str,
    batch_id: str | None,
    followup_token: str | None,
    active_test_email: str | None,
    message_id: str,
    delivery_email: str,
) -> None:
    with session_scope() as session:
        recipient = session.get(CampaignRecipient, int(recipient_id))
        if recipient is None:
            raise ValueError("recipient not found during finalize")

        if not active_test_email:
            extra = dict(recipient.extra or {})
            chain_state = dict(extra.get("chain") or {})
            chain_state["current_node_id"] = node_id
            extra["chain"] = chain_state
            recipient.extra = extra

            is_root = node_id == root_node_id
            if is_root and not followup_token:
                recipient.send_status = "in_chain"
            recipient.last_error = None

        if followup_token:
            mark_token_sent(followup_token)

        if batch_id and not active_test_email:
            record_delivery_attempt(
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                batch_id=batch_id,
                status="sent",
                provider_message_id=message_id,
                delivery_email=delivery_email,
            )
        session.flush()

    if not active_test_email:
        prewarm_next_node_documents(
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            current_node_id=node_id,
        )


def prewarm_next_node_documents(
    *,
    campaign_id: str,
    recipient_id: int,
    current_node_id: str,
) -> None:
    """Background-render documents for reachable next email nodes before the user clicks."""

    def _run() -> None:
        try:
            template_ids: list[str] = []
            with session_scope() as session:
                camp = session.get(Campaign, campaign_id)
                if camp is None or not camp.job_id:
                    return
                chain = get_email_chain(camp)
                node_by_id = {n["id"]: n for n in chain.get("nodes") or []}
                seen: set[str] = set()
                for edge in outgoing_edges(chain, current_node_id):
                    target = node_by_id.get(str(edge.get("target_id") or ""))
                    if target is None or not is_email_node(target):
                        continue
                    for doc_id in target.get("document_template_ids") or []:
                        key = str(doc_id or "").strip()
                        if key and key not in seen:
                            seen.add(key)
                            template_ids.append(key)
            if not template_ids:
                return
            from src.campaigns.template_render_service import ensure_recipient_templates_rendered

            ensure_recipient_templates_rendered(
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                template_ids=template_ids,
            )
        except Exception:
            logger.exception(
                "chain_prewarm_failed",
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                node_id=current_node_id,
            )

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"chain-prewarm-{campaign_id[:8]}-{recipient_id}",
    ).start()


def send_chain_node_email(
    *,
    campaign_id: str,
    recipient_id: int,
    node_id: str,
    batch_id: str | None = None,
    followup_token: str | None = None,
    hour_counts: dict[str, int] | None = None,
    day_counts: dict[str, int] | None = None,
    test_email: str | None = None,
    connection_id: str | None = None,
) -> dict[str, Any]:
    from src.campaigns.batch_worker import _send_delivery_message

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        recipient = session.get(CampaignRecipient, int(recipient_id))
        if camp is None or recipient is None:
            raise ValueError("campaign or recipient not found")
        chain = get_email_chain(camp)
        node = find_node(chain, node_id)
        if node is None:
            raise ValueError("chain node not found")
        if not is_email_node(node):
            raise ValueError("chain node is not an email block")

        active_test_email = test_email
        if followup_token:
            token_row = session.get(CampaignChainToken, followup_token)
            if token_row is not None and token_row.test_email:
                active_test_email = token_row.test_email

        delivery_email = active_test_email
        if delivery_email:
            from src.campaigns.recipient_email_service import validate_delivery_email

            validation_result = validate_delivery_email(delivery_email)
            if not validation_result.is_valid:
                raise ValueError(validation_result.reason or "Email не прошёл проверку SMTP.BZ.")
            delivery_email = validation_result.normalized_email
        else:
            from src.campaigns.recipient_email_service import (
                persist_delivery_email_state,
                resolve_delivery_email,
                validation_attempts_error,
            )

            delivery_email, validation_attempts = resolve_delivery_email(recipient)
            if not delivery_email:
                error_text = validation_attempts_error(validation_attempts)
                if followup_token:
                    mark_token_sent(followup_token, status="skipped", error=error_text)
                recipient.send_status = "failed"
                recipient.last_error = error_text
                session.flush()
                return {"status": "skipped", "reason": "invalid_email", "node_id": node_id}
            persist_delivery_email_state(recipient, delivery_email)

        from src.generator.delivery.suppression_store import is_suppressed

        suppression_target = delivery_email
        suppressed, suppress_reason = is_suppressed(suppression_target)
        if suppressed:
            if followup_token:
                mark_token_sent(followup_token, status="skipped")
            return {"status": "skipped", "reason": "suppressed", "node_id": node_id}

        owner = camp.owner_username
        job_id = camp.job_id
        root_node_id = str(chain.get("root_node_id") or "")

        if connection_id:
            resolved_connection_id = connection_id
            from src.campaigns.connection_service import resolve_connection

            resolved = resolve_connection(connection_id, owner, campaign=camp)
            send_transport = resolved.transport
        else:
            from src.campaigns.connection_service import campaign_connection_ids, pick_available_connection

            counters_hour = hour_counts if hour_counts is not None else {}
            counters_day = day_counts if day_counts is not None else {}
            connection = pick_available_connection(
                campaign_connection_ids(camp),
                owner,
                counters_hour,
                counters_day,
                campaign=camp,
            )
            if connection is None:
                raise RuntimeError("Все подключения исчерпали лимиты отправки")
            resolved_connection_id = connection.id
            connection_id = resolved_connection_id
            send_transport = connection.transport

        subject_template, body_html_template, body_text_template = _load_node_email_template(node, camp)
        email_template_id = str(node.get("email_template_id") or "") or None
        html, text = _render_body(
            body_html_template,
            recipient,
            camp,
            body_text_template,
            email_template_id=email_template_id,
        )
        from src.campaigns.template_render_service import render_email_template_text

        subject = render_email_template_text(
            subject_template,
            recipient=recipient,
            campaign=camp,
            template_id=email_template_id,
        )
        if active_test_email:
            subject = f"[TEST] {subject}"

        edges = outgoing_edges(chain, node_id)
        node_by_id = {n["id"]: n for n in chain.get("nodes") or []}

        from src.generator.generation.kp_one_page_fitter import KpLayoutError
        from src.campaigns.layout_send_utils import record_kp_layout_send_failure

        try:
            attachments = _resolve_document_attachments(
                list(node.get("document_template_ids") or []),
                campaign=camp,
                recipient=recipient,
            )
        except KpLayoutError as exc:
            if not active_test_email:
                record_kp_layout_send_failure(
                    campaign_id=campaign_id,
                    recipient=recipient,
                    campaign=camp,
                    batch_id=batch_id,
                    error=exc,
                    subject=subject,
                    send_mode="chain_followup" if followup_token else "chain_root",
                )
            if followup_token:
                mark_token_sent(followup_token, error=str(exc))
            session.commit()
            raise

        if batch_id and not active_test_email:
            record_delivery_attempt(
                campaign_id=campaign_id,
                recipient_id=int(recipient_id),
                batch_id=batch_id,
                status="sending",
                delivery_email=delivery_email,
            )

        campaign_for_send = camp

    buttons = _persist_chain_branch_tokens(
        campaign_id=campaign_id,
        recipient_id=int(recipient_id),
        source_node_id=node_id,
        edges=edges,
        node_by_id=node_by_id,
        test_email=active_test_email,
    )
    html, text = inject_chain_buttons(html, text, buttons)

    if buttons:
        public_base = str(getattr(settings, "public_base_url", "") or "").rstrip("/")
        first_token = buttons[0][1]
        logger.info(
            "chain_node_send_prepared",
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            node_id=node_id,
            token_count=len(buttons),
            public_base_url=public_base,
            first_chain_href=f"{public_base}/chain/branch/{first_token}",
        )

    try:
        message_id = _send_delivery_message(
            connection_id=resolved_connection_id,
            owner_username=owner,
            to_email=delivery_email,
            subject=subject,
            html=html,
            text=text,
            job_id=job_id,
            row_id=str(recipient_id),
            attachments=attachments,
            send_mode="chain_followup" if followup_token else "chain_root",
            send_run_id=followup_token,
            campaign=campaign_for_send,
            track_links=False,
        )
    except Exception as exc:
        _record_chain_send_failure(
            campaign_id=campaign_id,
            recipient_id=int(recipient_id),
            batch_id=batch_id,
            followup_token=followup_token,
            active_test_email=active_test_email,
            error=exc,
        )
        logger.exception(
            "chain_node_send_failed",
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            node_id=node_id,
        )
        raise

    try:
        _finalize_chain_send_success(
            campaign_id=campaign_id,
            recipient_id=int(recipient_id),
            node_id=node_id,
            root_node_id=root_node_id,
            batch_id=batch_id,
            followup_token=followup_token,
            active_test_email=active_test_email,
            message_id=message_id,
            delivery_email=delivery_email,
        )
        if not active_test_email:
            from datetime import datetime, timezone

            from src.campaigns.recipient_email_service import append_campaign_sent_mail_log
            from src.generator.delivery.manager_stats import invalidate_stats_cache

            with session_scope() as session:
                camp = session.get(Campaign, campaign_id)
                recipient = session.get(CampaignRecipient, int(recipient_id))
                if camp is not None and recipient is not None:
                    if append_campaign_sent_mail_log(
                        job_id=job_id,
                        campaign_id=campaign_id,
                        recipient_id=int(recipient_id),
                        recipient=recipient,
                        delivery_email=delivery_email,
                        provider_message_id=message_id,
                        transport=send_transport,
                        send_mode="chain_followup" if followup_token else "chain_root",
                        subject=subject,
                        campaign_name=camp.name,
                        sent_at=datetime.now(timezone.utc).isoformat(),
                        connection_id=resolved_connection_id,
                    ):
                        invalidate_stats_cache(job_id)
    except Exception:
        logger.exception(
            "chain_node_send_finalize_failed",
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            node_id=node_id,
            message_id=message_id,
        )
        raise

    if hour_counts is not None:
        hour_counts[connection_id] = hour_counts.get(connection_id, 0) + 1
    if day_counts is not None:
        day_counts[connection_id] = day_counts.get(connection_id, 0) + 1

    return {
        "status": "sent",
        "message_id": message_id,
        "node_id": node_id,
        "to": delivery_email,
        "test_email": active_test_email,
    }


def run_chain_followup(kwargs: dict[str, Any]) -> dict[str, Any]:
    token = str(kwargs.get("token") or "")
    return send_chain_node_email(
        campaign_id=str(kwargs.get("campaign_id") or ""),
        recipient_id=int(kwargs.get("recipient_id") or 0),
        node_id=str(kwargs.get("target_node_id") or ""),
        followup_token=token or None,
    )


def _claim_followup_token(token: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.execute(
            select(CampaignChainToken)
            .where(CampaignChainToken.token == token)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.send_status in {"sent", "sending"}:
            return None
        camp = session.get(Campaign, row.campaign_id)
        if camp is None:
            return None
        chain = get_email_chain(camp)
        target_node = find_node(chain, row.target_node_id)
        if target_node is None or is_link_node(target_node):
            return None
        recipient = session.get(CampaignRecipient, int(row.recipient_id))
        if recipient is not None:
            from src.generator.delivery.suppression_store import is_suppressed

            suppression_target = row.test_email or recipient.email
            suppressed, _reason = is_suppressed(suppression_target)
            if suppressed:
                row.send_status = "skipped"
                session.flush()
                return None
        row.send_status = "sending"
        session.flush()
        return {
            "token": row.token,
            "campaign_id": row.campaign_id,
            "recipient_id": row.recipient_id,
            "target_node_id": row.target_node_id,
            "job_id": camp.job_id or row.campaign_id,
            "owner_username": camp.owner_username,
        }


def _reset_followup_token_pending(token: str) -> None:
    with session_scope() as session:
        row = session.get(CampaignChainToken, token)
        if row is None or row.send_status == "sent":
            return
        row.send_status = "pending"
        row.error = None
        session.flush()


def _enqueue_chain_followup_fallback(payload: dict[str, Any]) -> None:
    from src.workers.task_queue import enqueue_task

    token = str(payload.get("token") or "")
    enqueue_task(
        task_type="chain_followup",
        job_id=str(payload.get("job_id") or ""),
        owner_username=str(payload.get("owner_username") or ""),
        payload={
            "token": token,
            "campaign_id": payload.get("campaign_id"),
            "recipient_id": payload.get("recipient_id"),
            "target_node_id": payload.get("target_node_id"),
        },
        idempotency_key=f"chain_followup:{token}",
        active_key=f"chain_followup:{token}",
        max_attempts=3,
        priority=_CHAIN_FOLLOWUP_PRIORITY,
    )


def _run_chain_followup_fast(payload: dict[str, Any]) -> None:
    token = str(payload.get("token") or "")
    try:
        run_chain_followup(payload)
    except Exception:
        logger.exception("chain_followup_fast_failed", token=token)
        _reset_followup_token_pending(token)
        _enqueue_chain_followup_fallback(payload)


def dispatch_chain_followup(token: str) -> None:
    payload = _claim_followup_token(token)
    if payload is None:
        return
    threading.Thread(
        target=_run_chain_followup_fast,
        args=(payload,),
        daemon=True,
        name=f"chain-followup-{token[:8]}",
    ).start()


def start_test_chain(
    campaign_id: str,
    to_email: str,
    owner_username: str,
    connection_id: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    from src.security.company_access import can_access_owner

    to_email = str(to_email or "").strip()
    if not to_email:
        raise ValueError("Укажите email для теста")

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise ValueError("Рассылка не найдена")
        if camp.send_scenario != "email_chain":
            raise ValueError("Рассылка не использует email-цепочку")

        recipient = session.scalar(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.excluded.is_(False),
            )
            .order_by(CampaignRecipient.row_index.asc())
            .limit(1)
        )
        if recipient is None:
            raise ValueError("Нет получателей для тестовой цепочки")

        chain = get_email_chain(camp, session=session)
        root_id = str(chain.get("root_node_id") or "")
        if not root_id:
            raise ValueError("В цепочке не задан корневой блок")

        recipient_id = int(recipient.id)
        recipient_preview = {
            "id": recipient_id,
            "company": recipient.company,
            "contact_name": recipient.contact_name,
            "email": recipient.email,
        }

    result = send_chain_node_email(
        campaign_id=campaign_id,
        recipient_id=recipient_id,
        node_id=root_id,
        test_email=to_email,
        connection_id=connection_id,
    )
    if result.get("status") != "sent":
        reason = str(result.get("reason") or "unknown")
        if reason == "suppressed":
            raise ValueError(f"Тестовый адрес {to_email} в стоп-листе")
        raise ValueError(f"Не удалось отправить тестовую цепочку: {reason}")

    return {
        "mode": "chain_test",
        "to": to_email,
        "message_id": result.get("message_id"),
        "node_id": result.get("node_id"),
        "recipient_preview": recipient_preview,
    }
