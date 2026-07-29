"""Render campaign emails for a specific recipient (statistics / full analytics)."""

from __future__ import annotations

from typing import Any

from src.campaigns.batch_worker import _load_email_template, _render_body
from src.campaigns.chain_preview_service import (
    _render_email_node_preview,
    _resolve_recipient,
    iter_email_nodes_bfs,
)
from src.campaigns.chain_service import get_email_chain
from src.campaigns.template_render_service import render_email_template_text
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient
from src.jobs.job_docs import read_sent_mail_log
from src.security.company_access import can_access_owner


def _resolve_recipient(session, campaign_id: str, recipient_id: int | None) -> CampaignRecipient:
    from src.campaigns.chain_preview_service import _resolve_recipient as resolve_recipient

    return resolve_recipient(session, campaign_id, recipient_id)


def _sent_at_for_recipient(job_id: str | None, recipient_id: int) -> str | None:
    if not job_id:
        return None
    for item in reversed(read_sent_mail_log(job_id)):
        if str(item.get("recipient_id") or "") == str(recipient_id):
            sent_at = str(item.get("sent_at") or "").strip()
            if sent_at:
                return sent_at
    return None


def _single_template_attachments(campaign: Campaign, recipient: CampaignRecipient) -> list[dict[str, Any]]:
    from src.campaigns.chain_preview_service import _preview_attachments

    template_ids: list[str] = []
    if campaign.kp_template_id:
        template_ids.append(str(campaign.kp_template_id))
    if campaign.contract_template_id:
        template_ids.append(str(campaign.contract_template_id))
    if not template_ids:
        return []
    items, _issues = _preview_attachments(template_ids, campaign=campaign, recipient=recipient)
    return items


def _render_single_template_preview(*, campaign: Campaign, recipient: CampaignRecipient) -> dict[str, Any]:
    from src.campaigns.chain_template_utils import strip_chain_button_placeholder

    subject_template, body_html_template, body_text_template = _load_email_template(campaign)
    html, text = _render_body(
        body_html_template,
        recipient,
        campaign,
        body_text_template,
        email_template_id=campaign.email_template_id,
    )
    html = strip_chain_button_placeholder(html)
    subject = render_email_template_text(
        subject_template,
        recipient=recipient,
        campaign=campaign,
        template_id=campaign.email_template_id,
    )
    attachments = _single_template_attachments(campaign, recipient)
    return {
        "node_id": "main",
        "node_name": "Основное письмо",
        "subject": subject,
        "body_html": html,
        "body_text": text,
        "email_template_id": campaign.email_template_id,
        "issues": [],
        "attachments": attachments,
    }


def preview_sent_email_for_recipient(
    campaign_id: str,
    owner_username: str,
    *,
    recipient_id: int | None = None,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise ValueError("Рассылка не найдена")

        recipient = _resolve_recipient(session, campaign_id, recipient_id)
        sent_at = _sent_at_for_recipient(camp.job_id, int(recipient.id))

        if camp.email_chain_id or camp.send_scenario == "email_chain":
            chain = get_email_chain(camp, session=session)
            email_nodes = iter_email_nodes_bfs(chain)
            if not email_nodes:
                raise ValueError("В цепочке нет email-блоков")
            items = [
                {
                    **_render_email_node_preview(node=node, chain=chain, campaign=camp, recipient=recipient),
                    "sent_at": sent_at,
                }
                for node in email_nodes
            ]
        else:
            item = _render_single_template_preview(campaign=camp, recipient=recipient)
            item["sent_at"] = sent_at
            items = [item]

        return {
            "recipient": {
                "id": recipient.id,
                "company": recipient.company,
                "contact_name": recipient.contact_name,
                "email": recipient.email,
            },
            "items": items,
        }
