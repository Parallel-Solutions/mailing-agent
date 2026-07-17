"""Render personalized templates for campaign recipients and cache results."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from src.campaigns import template_service
from src.campaigns.chain_service import get_email_chain
from src.campaigns.variable_match_service import _chain_template_ids, render_template_text
from src.generator.generation.document_builder import render_docx
from src.generator.generation.transforms import build_document_context
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient, MailTemplate, TemplateVersion
from src.infra.object_store import get_bytes
from src.jobs.json_store import read_json, write_json_atomic
from src.jobs.storage import resolve_job_paths

MANIFEST_FILENAME = "template-generation.json"
VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_path(job_id: str) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / MANIFEST_FILENAME


def _cache_dir(job_id: str, recipient_id: int) -> Path:
    path = resolve_job_paths(job_id).root_dir / "generated" / str(recipient_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(job_id: str, recipient_id: int, template_id: str, suffix: str) -> Path:
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return _cache_dir(job_id, recipient_id) / f"{template_id}{safe_suffix}"


def _extra_value(extra: dict[str, Any], key: str, *aliases: str) -> Any:
    normalized = {str(name).strip().upper(): value for name, value in extra.items()}
    for candidate in (key, *aliases):
        value = normalized.get(candidate.upper())
        if value not in (None, ""):
            return value
    return ""


def _recipient_row(recipient: CampaignRecipient) -> dict[str, Any]:
    from src.parser.excel_writer import COLUMNS

    extra = dict(recipient.extra or {})
    company = recipient.company or _extra_value(extra, "MUN_NAME", "ADM_NAME", "COMPANY")
    row: dict[str, Any] = {
        key: _extra_value(extra, key)
        for _, key in COLUMNS
        if key
    }
    row.update(
        {
            "ID": str(recipient.id),
            "SUB_RF": _extra_value(extra, "SUB_RF") or recipient.region,
            "MUN_NAME": company,
            "ADM_NAME": company,
            "EMAIL": recipient.email or recipient.email_fallback,
            "EMAIL_OSN": recipient.email or recipient.email_fallback,
            "HEAD_FIO": recipient.contact_name or _extra_value(extra, "HEAD_FIO", "CONTACT"),
        }
    )
    for key, value in extra.items():
        technical_key = str(key).strip().upper()
        if technical_key and technical_key not in row:
            row[technical_key] = value
    return row


def _build_context(recipient: CampaignRecipient, campaign: Campaign) -> dict[str, Any]:
    row = _recipient_row(recipient)
    context = build_document_context(row, outgoing_number=1, work_type=campaign.work_type or None)
    mapping = dict((campaign.draft_payload or {}).get("variable_mapping") or {})
    for var_name, column in mapping.items():
        from src.campaigns.variable_match_service import resolve_recipient_value

        value = resolve_recipient_value(recipient, column)
        if value:
            context[str(var_name).upper()] = value
            context[str(var_name)] = value
    return context


def _build_replacements(context: dict[str, Any], text: str) -> list[tuple[str, str]]:
    names = sorted(set(VARIABLE_RE.findall(text or "")))
    replacements: list[tuple[str, str]] = []
    for name in names:
        value = context.get(name) or context.get(name.upper()) or ""
        replacements.append((f"{{{{{name}}}}}", str(value)))
    return replacements


def collect_campaign_template_ids(campaign: Campaign) -> tuple[list[str], list[str]]:
    draft = dict(campaign.draft_payload or {})
    chain = get_email_chain(campaign)
    if chain.get("nodes"):
        email_ids: list[str] = []
        document_ids: list[str] = []
        seen_docs: set[str] = set()
        for raw in chain.get("nodes") or []:
            if not isinstance(raw, dict):
                continue
            email_id = str(raw.get("email_template_id") or "").strip()
            if email_id and email_id not in email_ids:
                email_ids.append(email_id)
            for doc_id in raw.get("document_template_ids") or []:
                doc_key = str(doc_id or "").strip()
                if doc_key and doc_key not in seen_docs:
                    seen_docs.add(doc_key)
                    document_ids.append(doc_key)
        return email_ids, document_ids
    return _chain_template_ids(draft)


def _load_template(template_id: str) -> tuple[MailTemplate, TemplateVersion] | None:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or not tmpl.active_version_id:
            return None
        version = session.get(TemplateVersion, tmpl.active_version_id)
        if version is None:
            return None
        session.expunge(tmpl)
        session.expunge(version)
        return tmpl, version


def _signature(campaign: Campaign, template_ids: list[str]) -> str:
    parts: list[dict[str, Any]] = []
    for template_id in sorted(template_ids):
        loaded = _load_template(template_id)
        if loaded is None:
            continue
        tmpl, version = loaded
        parts.append(
            {
                "template_id": template_id,
                "version_id": version.id,
                "is_template": bool(tmpl.is_template),
                "filename": version.filename,
            }
        )
    payload = {
        "campaign_id": campaign.id,
        "work_type": campaign.work_type or "",
        "templates": parts,
        "mapping": dict((campaign.draft_payload or {}).get("variable_mapping") or {}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _render_pdf_overlay(source_data: bytes, editor_state: dict[str, Any], context: dict[str, Any]) -> bytes:
    from src.campaigns.pdf_overlay_service import render_pdf

    state = deepcopy(editor_state)
    for field in state.get("fields") or []:
        variable = str(field.get("variable") or "").strip()
        if not variable:
            continue
        value = str(context.get(variable) or context.get(variable.upper()) or "")
        if value:
            field["value"] = value
    return render_pdf(source_data, state)


def _convert_docx_to_pdf(docx_path: Path, output_pdf: Path) -> Path:
    from src.generator.generation.template_preview import _convert_preview_docx_to_pdf

    converted = _convert_preview_docx_to_pdf(docx_path, output_pdf.parent)
    if converted is None or not converted.exists():
        raise RuntimeError("Не удалось преобразовать DOCX в PDF")
    if converted != output_pdf:
        output_pdf.write_bytes(converted.read_bytes())
    return output_pdf


def render_document_template_for_recipient(
    *,
    template_id: str,
    recipient: CampaignRecipient,
    campaign: Campaign,
    job_id: str,
    force: bool = False,
) -> tuple[str, bytes]:
    """Render one document template; returns (filename, bytes)."""
    loaded = _load_template(template_id)
    if loaded is None:
        raise FileNotFoundError(f"Шаблон {template_id} не найден")
    tmpl, version = loaded

    suffix = Path(str(version.filename or "document.pdf")).suffix.lower() or ".pdf"
    cache_file = _cache_path(job_id, int(recipient.id), template_id, suffix)
    pdf_cache = _cache_path(job_id, int(recipient.id), template_id, ".pdf")

    if not force:
        if pdf_cache.exists():
            return pdf_cache.name, pdf_cache.read_bytes()
        if cache_file.exists():
            return cache_file.name, cache_file.read_bytes()

    if not tmpl.is_template:
        filename = version.rendered_pdf_filename or version.filename or f"{tmpl.name}.pdf"
        data: bytes | None = None
        if version.rendered_pdf_storage_key:
            try:
                data = get_bytes(version.rendered_pdf_storage_key)
            except Exception:
                data = None
        if data is None and version.storage_key:
            data = get_bytes(version.storage_key)
        if data is None:
            raise RuntimeError(f"Файл шаблона {tmpl.name} недоступен")
        cache_file.write_bytes(data)
        return filename, data

    context = _build_context(recipient, campaign)
    source_key = version.storage_key
    source_name = str(version.filename or tmpl.name)
    if not source_key:
        raise RuntimeError(f"У шаблона {tmpl.name} нет исходного файла")

    source_data = get_bytes(source_key)
    source_suffix = Path(source_name).suffix.lower()

    if source_suffix == ".docx":
        text = template_service._file_text(source_name, source_data)  # noqa: SLF001
        replacements = _build_replacements(context, text)
        with TemporaryDirectory(prefix="template-render-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / Path(source_name).name
            output_docx = root / "rendered.docx"
            output_pdf = root / "rendered.pdf"
            source_path.write_bytes(source_data)
            render_docx(source_path, replacements, output_docx, context)
            _convert_docx_to_pdf(output_docx, output_pdf)
            pdf_cache.write_bytes(output_pdf.read_bytes())
            return pdf_cache.name, pdf_cache.read_bytes()

    if source_suffix == ".pdf":
        editor_state = version.editor_state if isinstance(version.editor_state, dict) else None
        if editor_state and editor_state.get("fields"):
            pdf_data = _render_pdf_overlay(source_data, editor_state, context)
        else:
            pdf_data = source_data
        pdf_cache.write_bytes(pdf_data)
        delivery_name = version.rendered_pdf_filename or f"{Path(source_name).stem}.pdf"
        return delivery_name, pdf_data

    cache_file.write_bytes(source_data)
    return cache_file.name, source_data


def ensure_recipient_templates_rendered(
    *,
    campaign_id: str,
    recipient_id: int,
    template_ids: list[str],
    force: bool = False,
) -> None:
    with session_scope() as session:
        campaign = session.get(Campaign, campaign_id)
        recipient = session.get(CampaignRecipient, int(recipient_id))
        if campaign is None or recipient is None:
            raise ValueError("campaign or recipient not found")
        if not campaign.job_id:
            raise ValueError("У рассылки не создано рабочее пространство")
        session.expunge(campaign)
        session.expunge(recipient)

    job_id = str(campaign.job_id)
    for template_id in template_ids:
        loaded = _load_template(template_id)
        if loaded is None:
            continue
        tmpl, _version = loaded
        if not tmpl.is_template:
            continue
        render_document_template_for_recipient(
            template_id=template_id,
            recipient=recipient,
            campaign=campaign,
            job_id=job_id,
            force=force,
        )


def pre_generate_batch_templates(
    *,
    campaign_id: str,
    recipient_ids: list[int],
    template_ids: list[str] | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError("campaign not found")
        session.expunge(campaign)

    _, document_ids = collect_campaign_template_ids(campaign)
    target_template_ids = list(template_ids or document_ids)
    personalized_ids = [
        template_id
        for template_id in target_template_ids
        if (loaded := _load_template(template_id)) is not None and loaded[0].is_template
    ]
    rendered = 0
    errors: list[str] = []
    for recipient_id in recipient_ids:
        for template_id in personalized_ids:
            try:
                with session_scope() as session:
                    recipient = session.get(CampaignRecipient, int(recipient_id))
                    if recipient is None:
                        continue
                    session.expunge(recipient)
                render_document_template_for_recipient(
                    template_id=template_id,
                    recipient=recipient,
                    campaign=campaign,
                    job_id=str(campaign.job_id or ""),
                )
                rendered += 1
            except Exception as exc:
                errors.append(f"recipient={recipient_id} template={template_id}: {exc}")

    manifest = {
        "updated_at": _now_iso(),
        "signature": _signature(campaign, personalized_ids),
        "template_ids": personalized_ids,
        "rendered": rendered,
        "errors": errors,
    }
    if campaign.job_id:
        write_json_atomic(_manifest_path(campaign.job_id), manifest)
    return manifest


def resolve_cached_attachment(
    *,
    template_id: str,
    recipient_id: int,
    job_id: str | None,
    owner_username: str,
    campaign: Campaign | None = None,
    recipient: CampaignRecipient | None = None,
) -> tuple[str, bytes] | None:
    loaded = _load_template(template_id)
    if loaded is None:
        return None
    tmpl, version = loaded
    if tmpl.owner_username != owner_username:
        return None

    if tmpl.is_template and campaign is not None and recipient is not None and job_id:
        try:
            filename, data = render_document_template_for_recipient(
                template_id=template_id,
                recipient=recipient,
                campaign=campaign,
                job_id=job_id,
            )
            return filename, data
        except Exception:
            pass

    if job_id:
        for suffix in (".pdf", ".docx"):
            cache_file = _cache_dir(job_id, recipient_id) / f"{template_id}{suffix}"
            if cache_file.exists():
                return cache_file.name, cache_file.read_bytes()

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
        return filename, data
    return None


def should_render_email_template(template_id: str | None) -> bool:
    if not template_id:
        return True
    loaded = _load_template(str(template_id))
    if loaded is None:
        return True
    return bool(loaded[0].is_template)


def render_email_template_text(
    text: str,
    *,
    recipient: CampaignRecipient,
    campaign: Campaign,
    template_id: str | None,
) -> str:
    if not should_render_email_template(template_id):
        return text
    return render_template_text(text, recipient=recipient, campaign=campaign)


def load_manifest(job_id: str) -> dict[str, Any]:
    result = read_json(_manifest_path(job_id), default={})
    return result.data if result.ok and isinstance(result.data, dict) else {}
