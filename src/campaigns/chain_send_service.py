"""Send emails for email-chain nodes with branch buttons and document attachments."""

from __future__ import annotations

from typing import Any

from src.campaigns.chain_service import (
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
from src.infra.object_store import get_bytes
from src.utils.logger import logger


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
) -> tuple[str, str]:
    from src.campaigns.variable_match_service import render_template_text

    html = template_html or (
        f"<p>Здравствуйте, {recipient.contact_name or 'коллеги'}!</p>"
        f"<p>{campaign.description or campaign.name}</p>"
    )
    html = render_template_text(html, recipient=recipient, campaign=campaign)
    if template_text.strip():
        text = render_template_text(template_text, recipient=recipient, campaign=campaign)
    else:
        text = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<p>", "").replace("</p>", "\n")
    return html, text


def _resolve_document_attachments(
    document_template_ids: list[str],
) -> list[tuple[str, bytes]]:
    attachments: list[tuple[str, bytes]] = []
    if not document_template_ids:
        return attachments
    with session_scope() as session:
        for template_id in document_template_ids:
            tmpl = session.get(MailTemplate, str(template_id))
            if tmpl is None or not tmpl.active_version_id:
                continue
            version = session.get(TemplateVersion, tmpl.active_version_id)
            if version is None:
                continue
            filename = version.rendered_pdf_filename or version.filename or f"{tmpl.name}.pdf"
            data: bytes | None = None
            if version.rendered_pdf_storage_key:
                try:
                    data = get_bytes(version.rendered_pdf_storage_key)
                except Exception:
                    data = None
            if data is None and version.storage_key:
                try:
                    data = get_bytes(version.storage_key)
                except Exception:
                    data = None
            if data:
                attachments.append((filename, data))
    return attachments


def send_chain_node_email(
    *,
    campaign_id: str,
    recipient_id: int,
    node_id: str,
    batch_id: str | None = None,
    followup_token: str | None = None,
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
        from src.generator.delivery.suppression_store import is_suppressed

        suppressed, _reason = is_suppressed(recipient.email)
        if suppressed:
            if followup_token:
                mark_token_sent(followup_token, status="skipped")
            return {"status": "skipped", "reason": "suppressed", "node_id": node_id}
        connection_id = str(camp.smtp_mailbox_id or "")
        owner = camp.owner_username
        job_id = camp.job_id

        subject_template, body_html_template, body_text_template = _load_node_email_template(node, camp)
        html, text = _render_body(body_html_template, recipient, camp, body_text_template)
        from src.campaigns.variable_match_service import render_template_text

        subject = render_template_text(subject_template, recipient=recipient, campaign=camp)

        edges = outgoing_edges(chain, node_id)
        node_by_id = {n["id"]: n for n in chain.get("nodes") or []}
        token_rows: list[CampaignChainToken] = []
        if edges:
            token_rows = create_branch_tokens(
                campaign_id=campaign_id,
                recipient_id=int(recipient_id),
                source_node_id=node_id,
                edges=edges,
            )
            for row in token_rows:
                session.add(row)
            session.flush()

        buttons = [
            (resolve_button_label(edge, node_by_id), row.token)
            for edge, row in zip(edges, token_rows, strict=True)
        ]
        html, text = inject_chain_buttons(html, text, buttons)
        attachments = _resolve_document_attachments(list(node.get("document_template_ids") or []))

        if batch_id:
            record_delivery_attempt(
                campaign_id=campaign_id,
                recipient_id=int(recipient_id),
                batch_id=batch_id,
                status="sending",
            )

        try:
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
            )
            extra = dict(recipient.extra or {})
            chain_state = dict(extra.get("chain") or {})
            chain_state["current_node_id"] = node_id
            extra["chain"] = chain_state
            recipient.extra = extra

            is_root = node_id == chain.get("root_node_id")
            if is_root and not followup_token:
                recipient.send_status = "in_chain"
            elif followup_token:
                mark_token_sent(followup_token)
            recipient.last_error = None

            if batch_id:
                record_delivery_attempt(
                    campaign_id=campaign_id,
                    recipient_id=int(recipient_id),
                    batch_id=batch_id,
                    status="sent",
                    provider_message_id=message_id,
                )
            session.flush()
            return {"status": "sent", "message_id": message_id, "node_id": node_id}
        except Exception as exc:
            if followup_token:
                mark_token_sent(followup_token, error=str(exc))
            if batch_id:
                record_delivery_attempt(
                    campaign_id=campaign_id,
                    recipient_id=int(recipient_id),
                    batch_id=batch_id,
                    status="failed",
                    error=str(exc),
                )
            recipient.last_error = str(exc)
            session.flush()
            logger.exception(
                "chain_node_send_failed",
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                node_id=node_id,
            )
            raise


def run_chain_followup(kwargs: dict[str, Any]) -> dict[str, Any]:
    token = str(kwargs.get("token") or "")
    return send_chain_node_email(
        campaign_id=str(kwargs.get("campaign_id") or ""),
        recipient_id=int(kwargs.get("recipient_id") or 0),
        node_id=str(kwargs.get("target_node_id") or ""),
        followup_token=token or None,
    )


def dispatch_chain_followup(token: str) -> None:
    from src.workers.task_queue import enqueue_task

    with session_scope() as session:
        row = session.get(CampaignChainToken, token)
        if row is None:
            return
        camp = session.get(Campaign, row.campaign_id)
        if camp is None:
            return
        if row.send_status == "sent":
            return
        chain = get_email_chain(camp)
        target_node = find_node(chain, row.target_node_id)
        if target_node is None or is_link_node(target_node):
            return
        recipient = session.get(CampaignRecipient, int(row.recipient_id))
        if recipient is not None:
            from src.generator.delivery.suppression_store import is_suppressed

            suppressed, _reason = is_suppressed(recipient.email)
            if suppressed:
                from src.campaigns.chain_service import mark_token_sent

                mark_token_sent(token, status="skipped")
                return
        enqueue_task(
            task_type="chain_followup",
            job_id=camp.job_id or row.campaign_id,
            owner_username=camp.owner_username,
            payload={
                "token": row.token,
                "campaign_id": row.campaign_id,
                "recipient_id": row.recipient_id,
                "target_node_id": row.target_node_id,
            },
            idempotency_key=f"chain_followup:{row.token}",
            active_key=f"chain_followup:{row.token}",
            max_attempts=3,
        )
