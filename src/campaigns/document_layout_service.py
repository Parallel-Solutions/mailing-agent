"""Review and apply safe PDF text-layout corrections at campaign launch."""

from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
from typing import Any

import fitz
from sqlalchemy import select

from src.campaigns import template_service
from src.campaigns.pdf_overlay_service import (
    PDF_AUTO_LAYOUT_VERSION,
    build_auto_layout_state,
    render_pdf,
    render_pdf_with_discovered_placeholders,
    resolve_layout_field_value,
    save_generated_editor_state,
)
from src.campaigns.substitution_engine import discover_placeholders
from src.campaigns.template_render_service import (
    _build_context,
    _render_pdf_overlay,
    collect_campaign_template_ids,
    resolve_cached_attachment,
)
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient, MailTemplate, TemplateVersion
from src.infra.object_store import get_bytes
from src.security.company_access import can_access_owner

AUTO_LAYOUT_VERSION = PDF_AUTO_LAYOUT_VERSION
PREVIEW_SCALE = 1.35


def _load_review_context(
    campaign_id: str,
    visible_owners: frozenset[str] | None,
) -> tuple[Campaign, CampaignRecipient]:
    with session_scope() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None or not can_access_owner(visible_owners, campaign.owner_username):
            raise ValueError("Рассылка не найдена")
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
            raise ValueError("Нет получателей для проверки документа")
        session.expunge(campaign)
        session.expunge(recipient)
        return campaign, recipient


def _load_document_template(
    template_id: str,
    owner_username: str,
) -> tuple[MailTemplate, TemplateVersion] | None:
    with session_scope() as session:
        template = session.get(MailTemplate, template_id)
        if (
            template is None
            or template.owner_username != owner_username
            or not template.active_version_id
        ):
            return None
        version = session.get(TemplateVersion, template.active_version_id)
        if version is None:
            return None
        filename = str(version.filename or template.name or "")
        if Path(filename).suffix.lower() not in {".pdf", ".docx", ".html", ".htm"}:
            return None
        session.expunge(template)
        session.expunge(version)
        return template, version


def _pdf_text(data: bytes) -> str:
    document = fitz.open(stream=data, filetype="pdf")
    try:
        return "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()


def _preview_data_url(data: bytes) -> str:
    document = fitz.open(stream=data, filetype="pdf")
    try:
        if document.page_count == 0:
            raise ValueError("PDF не содержит страниц")
        pixmap = document[0].get_pixmap(
            matrix=fitz.Matrix(PREVIEW_SCALE, PREVIEW_SCALE),
            alpha=False,
        )
        encoded = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    finally:
        document.close()


def _resolved_state(state: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    resolved = deepcopy(state)
    for field in resolved.get("fields") or []:
        value = resolve_layout_field_value(field, context)
        if value:
            field["value"] = value
    return resolved


def _review_template(
    *,
    template: MailTemplate,
    version: TemplateVersion,
    campaign: Campaign,
    recipient: CampaignRecipient,
) -> dict[str, Any]:
    source_data = get_bytes(str(version.storage_key or ""))
    source_filename = str(version.filename or template.name or "document.pdf")
    if Path(source_filename).suffix.lower() != ".pdf":
        resolved = resolve_cached_attachment(
            template_id=str(template.id),
            recipient_id=int(recipient.id),
            job_id=str(campaign.job_id or campaign.id),
            owner_username=campaign.owner_username,
            campaign=campaign,
            recipient=recipient,
            strict=True,
        )
        if resolved is None:
            raise ValueError("\u0412\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e")
        preview_filename, preview_content = resolved
        if Path(preview_filename).suffix.lower() != ".pdf":
            preview_content, preview_filename = template_service._build_document_pdf_artifact(  # noqa: SLF001
                preview_filename,
                preview_content,
                owner_username=campaign.owner_username,
            )
        return {
            "template_id": str(template.id),
            "active_version_id": str(version.id),
            "template_name": str(template.name or version.filename or template.id),
            "filename": preview_filename,
            "status": "preview_only",
            "message": "\u041f\u043e\u043a\u0430\u0437\u0430\u043d \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0439 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442 \u0434\u043b\u044f \u043f\u0435\u0440\u0432\u043e\u0433\u043e \u043f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u044f.",
            "changes": [],
            "before_image": _preview_data_url(preview_content),
            "can_apply": False,
        }

    source_text = template_service.cached_version_source_text(version) or _pdf_text(source_data)
    placeholders = discover_placeholders(source_text)
    base = {
        "template_id": str(template.id),
        "active_version_id": str(version.id),
        "template_name": str(template.name or version.filename or template.id),
        "filename": str(version.filename or template.name or "document.pdf"),
    }
    if not placeholders:
        return {
            **base,
            "status": "skipped",
            "message": "В PDF не найдены подставляемые поля.",
            "changes": [],
            "can_apply": False,
        }

    context = _build_context(
        recipient,
        campaign,
        template_id=str(template.id),
        template_name=str(template.name or ""),
        template_text=source_text,
        allocate_document_id=False,
    )
    current_state = dict(version.editor_state or {})
    if current_state.get("fields"):
        current_pdf = _render_pdf_overlay(source_data, current_state, context)
    else:
        current_pdf = render_pdf_with_discovered_placeholders(
            source_data,
            placeholders,
            context,
            corporate_layout=False,
        )

    current_layout_version = str(
        (current_state.get("auto_layout") or {}).get("version") or ""
    )
    if current_layout_version == AUTO_LAYOUT_VERSION:
        changes = list((current_state.get("auto_layout") or {}).get("changes") or [])
        preview = _preview_data_url(current_pdf)
        return {
            **base,
            "status": "already_applied",
            "message": "Автоматическая коррекция уже применена к активной версии шаблона.",
            "changes": changes,
            "before_image": preview,
            "after_image": preview,
            "can_apply": False,
            "layout_version": AUTO_LAYOUT_VERSION,
        }

    candidate_state = build_auto_layout_state(source_data, placeholders, context)
    if not candidate_state.get("fields"):
        return {
            **base,
            "status": "skipped",
            "message": "Не удалось построить безопасную автоматическую разметку.",
            "changes": [],
            "can_apply": False,
        }
    candidate_pdf = render_pdf(source_data, _resolved_state(candidate_state, context))
    changes = list((candidate_state.get("auto_layout") or {}).get("changes") or [])
    return {
        **base,
        "status": "candidate",
        "message": "Сравните варианты и примените исправление, если результат подходит.",
        "changes": changes,
        "before_image": _preview_data_url(current_pdf),
        "after_image": _preview_data_url(candidate_pdf),
        "can_apply": True,
        "layout_version": AUTO_LAYOUT_VERSION,
    }


def inspect_campaign_layout(
    campaign_id: str,
    _owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    campaign, recipient = _load_review_context(campaign_id, visible_owners)
    _email_ids, document_ids = collect_campaign_template_ids(campaign)
    documents: list[dict[str, Any]] = []
    for template_id in document_ids:
        loaded = _load_document_template(str(template_id), campaign.owner_username)
        if loaded is None:
            continue
        template, version = loaded
        try:
            documents.append(
                _review_template(
                    template=template,
                    version=version,
                    campaign=campaign,
                    recipient=recipient,
                )
            )
        except Exception as exc:
            documents.append(
                {
                    "template_id": str(template.id),
                    "active_version_id": str(version.id),
                    "template_name": str(template.name or template.id),
                    "filename": str(version.filename or template.name or "document.pdf"),
                    "status": "error",
                    "message": str(exc),
                    "changes": [],
                    "can_apply": False,
                }
            )

    return {
        "campaign_id": campaign_id,
        "recipient": {
            "id": int(recipient.id),
            "company": str(recipient.company or ""),
            "contact_name": str(recipient.contact_name or ""),
        },
        "estimate_seconds": max(8, min(45, len(documents) * 8)),
        "documents": documents,
    }


def apply_campaign_layout(
    campaign_id: str,
    template_id: str,
    _owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    campaign, recipient = _load_review_context(campaign_id, visible_owners)
    _email_ids, document_ids = collect_campaign_template_ids(campaign)
    if template_id not in {str(item) for item in document_ids}:
        raise ValueError("Шаблон документа не связан с этой рассылкой")
    loaded = _load_document_template(template_id, campaign.owner_username)
    if loaded is None:
        raise ValueError("PDF-шаблон не найден")
    template, version = loaded
    if Path(str(version.filename or template.name or "")).suffix.lower() != ".pdf":
        raise ValueError("\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u043a\u043e\u0440\u0440\u0435\u043a\u0446\u0438\u044f \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0434\u043b\u044f PDF-\u0448\u0430\u0431\u043b\u043e\u043d\u043e\u0432")
    source_data = get_bytes(str(version.storage_key or ""))
    source_text = template_service.cached_version_source_text(version) or _pdf_text(source_data)
    placeholders = discover_placeholders(source_text)
    if not placeholders:
        raise ValueError("В PDF не найдены подставляемые поля")
    context = _build_context(
        recipient,
        campaign,
        template_id=template_id,
        template_name=str(template.name or ""),
        template_text=source_text,
        allocate_document_id=False,
    )
    state = build_auto_layout_state(source_data, placeholders, context)
    if not state.get("fields"):
        raise ValueError("Не удалось построить безопасную автоматическую разметку")
    saved = save_generated_editor_state(
        template_id,
        campaign.owner_username,
        _resolved_state(state, context),
    )
    return {
        "template_id": template_id,
        "template_version_id": str(saved.get("active_version_id") or ""),
        "layout_version": AUTO_LAYOUT_VERSION,
        "changes": list((state.get("auto_layout") or {}).get("changes") or []),
    }
