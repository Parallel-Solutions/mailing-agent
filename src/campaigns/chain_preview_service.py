"""Preview rendered email-chain nodes for a campaign recipient."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.campaigns.chain_send_service import _load_node_email_template, _render_body
from src.campaigns.chain_service import (
    get_email_chain,
    is_email_node,
    outgoing_edges,
    resolve_button_label,
)
from src.campaigns.chain_template_utils import inject_chain_buttons
from src.campaigns.template_render_service import render_email_template_text, resolve_cached_attachment
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient
from src.security.company_access import can_access_owner


def iter_email_nodes_bfs(chain: dict[str, Any]) -> list[dict[str, Any]]:
    root_id = str(chain.get("root_node_id") or "")
    nodes = list(chain.get("nodes") or [])
    node_by_id = {str(n.get("id")): n for n in nodes}
    visited: set[str] = set()
    queue = [root_id] if root_id else []
    ordered: list[dict[str, Any]] = []

    while queue:
        node_id = queue.pop(0)
        if not node_id or node_id in visited:
            continue
        visited.add(node_id)
        node = node_by_id.get(node_id)
        if node is None:
            continue
        if is_email_node(node):
            ordered.append(node)
        for edge in outgoing_edges(chain, node_id):
            target_id = str(edge.get("target_id") or "")
            if target_id and target_id not in visited:
                queue.append(target_id)

    for node in nodes:
        node_id = str(node.get("id") or "")
        if is_email_node(node) and node_id not in visited:
            ordered.append(node)
    return ordered


_TEXT_PREVIEW_LIMIT = 1500


def _content_type_for_filename(filename: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix in {".html", ".htm"}:
        return "text/html"
    return "application/octet-stream"


def _serialize_review_issue(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": str(item.get("fragment") or item.get("token") or ""),
        "kind": str(item.get("kind") or "artifact"),
        "field": str(item.get("field") or "body_html"),
        "severity": str(item.get("severity") or "error"),
        "message": str(item.get("message") or ""),
        "fragment": str(item.get("fragment") or item.get("token") or ""),
        "template_id": str(item.get("template_id") or "") or None,
        "suggestion": str(item.get("suggestion") or ""),
    }


def _first_preview_recipient(session, campaign_id: str) -> CampaignRecipient | None:
    return session.scalar(
        select(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.excluded.is_(False),
        )
        .order_by(CampaignRecipient.row_index.asc())
        .limit(1)
    )


def _resolve_recipient(session, campaign_id: str, recipient_id: int | None) -> CampaignRecipient:
    if recipient_id is not None:
        recipient = session.get(CampaignRecipient, int(recipient_id))
        if recipient is None or recipient.campaign_id != campaign_id:
            raise ValueError("Получатель не найден")
        return recipient
    recipient = _first_preview_recipient(session, campaign_id)
    if recipient is None:
        raise ValueError("Нет получателей для препросмотра")
    return recipient


def _preview_attachments(
    document_template_ids: list[str],
    *,
    campaign: Campaign,
    recipient: CampaignRecipient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from src.campaigns import template_service
    from src.campaigns.template_text_review_service import review_document_text

    items: list[dict[str, Any]] = []
    attachment_issues: list[dict[str, Any]] = []
    owner = campaign.owner_username
    job_id = campaign.job_id
    for template_id in document_template_ids:
        tid = str(template_id)
        try:
            resolved = resolve_cached_attachment(
                template_id=tid,
                recipient_id=int(recipient.id),
                job_id=str(job_id or campaign.id),
                owner_username=owner,
                campaign=campaign,
                recipient=recipient,
                strict=True,
            )
            if resolved:
                filename, data = resolved
                tmpl = template_service.get_template(tid, owner)
                template_name = str((tmpl or {}).get("name") or filename or tid)
                try:
                    doc_text = template_service._file_text(filename, data)  # noqa: SLF001
                except Exception:
                    doc_text = ""
                per_attachment_issues = review_document_text(
                    doc_text,
                    template_id=tid,
                    template_name=template_name,
                    field="attachment",
                )
                attachment_issues.extend(per_attachment_issues)
                text_preview = doc_text[:_TEXT_PREVIEW_LIMIT] if doc_text else ""
                items.append(
                    {
                        "template_id": tid,
                        "filename": filename,
                        "has_content": True,
                        "content_type": _content_type_for_filename(filename),
                        "text_preview": text_preview,
                        "issues": [_serialize_review_issue(issue) for issue in per_attachment_issues],
                    }
                )
            else:
                items.append(
                    {
                        "template_id": tid,
                        "filename": "",
                        "has_content": False,
                        "error": "Вложение не найдено",
                        "issues": [],
                    }
                )
        except Exception as exc:
            items.append(
                {
                    "template_id": tid,
                    "filename": "",
                    "has_content": False,
                    "error": str(exc),
                    "issues": [],
                }
            )
    return items, attachment_issues


def _render_email_node_preview(
    *,
    node: dict[str, Any],
    chain: dict[str, Any],
    campaign: Campaign,
    recipient: CampaignRecipient,
) -> dict[str, Any]:
    node_id = str(node.get("id") or "")
    subject_template, body_html_template, body_text_template = _load_node_email_template(node, campaign)
    email_template_id = str(node.get("email_template_id") or "") or None
    html, text = _render_body(
        body_html_template,
        recipient,
        campaign,
        body_text_template,
        email_template_id=email_template_id,
    )
    subject = render_email_template_text(
        subject_template,
        recipient=recipient,
        campaign=campaign,
        template_id=email_template_id,
    )

    edges = outgoing_edges(chain, node_id)
    node_by_id = {str(n["id"]): n for n in chain.get("nodes") or []}
    buttons = [
        (
            resolve_button_label(edge, node_by_id),
            "#",
            str((node_by_id.get(str(edge.get("target_id") or "")) or {}).get("link_kind") or "custom"),
        )
        for edge in edges
    ]
    html, _text = inject_chain_buttons(html, text, buttons)

    from src.campaigns.template_text_review_service import review_rendered_template

    review_issues = review_rendered_template(
        template_id=email_template_id,
        template_name=str(node.get("name") or "Письмо"),
        subject_template=subject_template,
        body_html_template=body_html_template,
        body_text_template=body_text_template,
        recipient=recipient,
        campaign=campaign,
        deep=False,
        rendered_subject=subject,
        rendered_html=html,
        rendered_text=text,
        include_placeholder_issues=True,
        strict_preview=True,
    )
    attachments, attachment_issues = _preview_attachments(
        list(node.get("document_template_ids") or []),
        campaign=campaign,
        recipient=recipient,
    )
    all_review_issues = [*review_issues, *attachment_issues]
    issues = [_serialize_review_issue(item) for item in all_review_issues]

    return {
        "node_id": node_id,
        "node_name": str(node.get("name") or "Письмо"),
        "subject": subject,
        "body_html": html,
        "email_template_id": email_template_id,
        "issues": issues,
        "attachments": attachments,
    }


def preview_chain_for_campaign(
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

        chain = get_email_chain(camp, session=session)
        email_nodes = iter_email_nodes_bfs(chain)
        if not email_nodes:
            raise ValueError("В цепочке нет email-блоков")

        items = [
            _render_email_node_preview(node=node, chain=chain, campaign=camp, recipient=recipient)
            for node in email_nodes
        ]
        return {
            "recipient": {
                "id": recipient.id,
                "company": recipient.company,
                "contact_name": recipient.contact_name,
                "email": recipient.email,
            },
            "items": items,
        }


def resolve_preview_attachment(
    campaign_id: str,
    recipient_id: int,
    template_id: str,
    owner_username: str,
    *,
    as_pdf: bool = False,
    visible_owners: frozenset[str] | None = None,
) -> tuple[str, bytes] | None:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise ValueError("Рассылка не найдена")
        recipient = session.get(CampaignRecipient, int(recipient_id))
        if recipient is None or recipient.campaign_id != campaign_id:
            raise ValueError("Получатель не найден")
        resolved = resolve_cached_attachment(
            template_id=str(template_id),
            recipient_id=int(recipient_id),
            job_id=str(camp.job_id or camp.id),
            owner_username=camp.owner_username,
            campaign=camp,
            recipient=recipient,
            strict=True,
        )
        if resolved is None or not as_pdf:
            return resolved

        filename, content = resolved
        if Path(filename).suffix.lower() == ".pdf":
            return filename, content

        from src.campaigns import template_service

        pdf_content, pdf_filename = template_service._build_document_pdf_artifact(  # noqa: SLF001
            filename,
            content,
            owner_username=camp.owner_username,
        )
        return pdf_filename, pdf_content
