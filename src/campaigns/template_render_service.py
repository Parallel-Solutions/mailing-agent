"""Render personalized templates for campaign recipients and cache results."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from src.campaigns import template_service
from src.campaigns.chain_service import get_email_chain
from src.campaigns.substitution_context import build_substitution_context
from src.campaigns.substitution_engine import build_replacement_pairs, discover_placeholders
from src.campaigns.variable_match_service import _chain_template_ids, render_template_text
from src.generator.generation.document_builder import DOCUMENT_RENDERER_VERSION, build_kp_replacements, render_docx
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient, MailTemplate, TemplateVersion
from src.infra.object_store import get_bytes
from src.jobs.json_store import read_json, write_json_atomic
from src.jobs.storage import resolve_job_paths

MANIFEST_FILENAME = "template-generation.json"


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


def _template_render_metadata(template_id: str) -> tuple[str, str]:
    loaded = _load_template(template_id)
    if loaded is None:
        return "", ""
    tmpl, version = loaded
    template_name = str(tmpl.name or "")
    text = template_service.cached_version_source_text(version)
    return template_name, text


def _build_context(
    recipient: CampaignRecipient,
    campaign: Campaign,
    *,
    template_id: str | None = None,
    template_name: str = "",
    template_text: str = "",
    allocate_document_id: bool = False,
) -> dict[str, str]:
    return build_substitution_context(
        recipient=recipient,
        campaign=campaign,
        outgoing_number=recipient.row_index or 1,
        template_id=template_id,
        template_name=template_name,
        template_text=template_text,
        allocate_document_id=allocate_document_id,
    )


def _build_replacements(context: dict[str, str], text: str, *, source_path: Path | None = None) -> list[tuple[str, str]]:
    pairs: dict[str, str] = {}
    if source_path is not None:
        from src.generator.generation.pdf_safe import is_kp_docx

        if is_kp_docx(source_path):
            for token, value in build_kp_replacements(context):
                pairs[token] = value
    for token, value in build_replacement_pairs(context, text):
        pairs[token] = value
    return sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True)


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


def _resolve_delivery_filename(
    tmpl: MailTemplate,
    version: TemplateVersion,
    *,
    source_name: str | None = None,
) -> str:
    stem_source = source_name or version.filename or tmpl.name or "document"
    if str(tmpl.attachment_output_format or "original") == "pdf":
        return version.rendered_pdf_filename or f"{Path(stem_source).stem}.pdf"
    return version.filename or Path(stem_source).name


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
                "attachment_output_format": str(
                    tmpl.attachment_output_format or "original"
                ),
                "filename": version.filename,
            }
        )
    payload = {
        "campaign_id": campaign.id,
        "work_type": campaign.work_type or "",
        "renderer_version": DOCUMENT_RENDERER_VERSION,
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


def _convert_docx_to_pdf(
    docx_path: Path,
    output_pdf: Path,
    *,
    file_kind: str | None = None,
    fontconfig_path: Path | str | None = None,
    prefer_local: bool = False,
) -> Path:
    from src.generator.generation.template_preview import convert_docx_to_delivery_pdf

    conversion_options: dict[str, Any] = {
        "file_kind": file_kind,
        "template_docx": docx_path,
    }
    if fontconfig_path or prefer_local:
        conversion_options.update(
            {
                "fontconfig_path": fontconfig_path,
                "prefer_local": prefer_local,
            }
        )
    return convert_docx_to_delivery_pdf(docx_path, output_pdf, **conversion_options)


def _meta_cache_path(pdf_cache: Path) -> Path:
    return pdf_cache.with_suffix(".meta.json")


def _read_render_meta(pdf_cache: Path) -> dict[str, Any] | None:
    meta_path = _meta_cache_path(pdf_cache)
    if not meta_path.exists():
        return None
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_render_meta(
    pdf_cache: Path,
    *,
    fit_result: Any | None = None,
    font_pack_hash: str = "",
    template_version_id: str = "",
) -> None:
    payload: dict[str, Any] = {"renderer_version": DOCUMENT_RENDERER_VERSION}
    if font_pack_hash:
        payload["font_pack_hash"] = font_pack_hash
    if template_version_id:
        payload["template_version_id"] = template_version_id
    if fit_result is not None:
        payload["font_half_points"] = int(fit_result.font_half_points)
    _meta_cache_path(pdf_cache).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _cached_pdf_is_valid(
    pdf_cache: Path,
    *,
    expected_font_pack_hash: str = "",
    expected_template_version_id: str = "",
    require_meta: bool = False,
) -> bool:
    if not pdf_cache.exists():
        return False
    meta = _read_render_meta(pdf_cache)
    if meta is None:
        return not require_meta
    if str(meta.get("renderer_version") or "") != DOCUMENT_RENDERER_VERSION:
        return False
    if expected_font_pack_hash and str(meta.get("font_pack_hash") or "") != expected_font_pack_hash:
        return False
    if (
        expected_template_version_id
        and str(meta.get("template_version_id") or "") != expected_template_version_id
    ):
        return False
    return True


def _convert_kp_docx_to_pdf(
    docx_path: Path,
    output_pdf: Path,
    *,
    file_kind: str | None,
    company: str,
    fontconfig_path: Path | str | None = None,
    prefer_local: bool = False,
    font_pack_hash: str = "",
    template_version_id: str = "",
) -> None:
    from src.generator.generation.kp_one_page_fitter import fit_docx_to_one_page_pdf

    fit_result = fit_docx_to_one_page_pdf(
        docx_path,
        output_pdf,
        file_kind=file_kind,
        template_docx=docx_path,
        company=company,
        fontconfig_path=fontconfig_path,
        prefer_local=prefer_local,
    )
    _write_render_meta(
        output_pdf,
        fit_result=fit_result,
        font_pack_hash=font_pack_hash,
        template_version_id=template_version_id,
    )


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

    source_name = str(version.filename or tmpl.name)
    delivery_name = _resolve_delivery_filename(tmpl, version, source_name=source_name)
    wants_pdf = str(tmpl.attachment_output_format or "original") == "pdf"
    expected_font_pack_hash = ""
    if suffix == ".docx":
        from src.campaigns.font_service import template_font_pack_hash

        expected_font_pack_hash = template_font_pack_hash(template_id, tmpl.owner_username)

    if not force:
        if wants_pdf and _cached_pdf_is_valid(
            pdf_cache,
            expected_font_pack_hash=expected_font_pack_hash,
            expected_template_version_id=str(version.id),
            require_meta=bool(tmpl.is_template),
        ):
            return delivery_name, pdf_cache.read_bytes()
        if not wants_pdf and cache_file.exists():
            if (
                suffix != ".pdf"
                or not tmpl.is_template
                or _cached_pdf_is_valid(
                    cache_file,
                    expected_template_version_id=str(version.id),
                    require_meta=True,
                )
            ):
                return delivery_name, cache_file.read_bytes()

    if not tmpl.is_template:
        filename = delivery_name
        data: bytes | None = None
        source_suffix = Path(str(version.filename or "")).suffix.lower()
        if wants_pdf and source_suffix == ".docx":
            item = template_service.get_template_delivery_file(
                template_id,
                tmpl.owner_username,
            )
            if item is not None:
                data = bytes(item["content"])
                filename = str(item["filename"])
        if wants_pdf and data is None and version.rendered_pdf_storage_key:
            try:
                data = get_bytes(version.rendered_pdf_storage_key)
            except Exception:
                data = None
        if wants_pdf and data is None:
            item = template_service.get_template_delivery_file(
                template_id,
                tmpl.owner_username,
            )
            if item is not None:
                data = bytes(item["content"])
                filename = str(item["filename"])
        if not wants_pdf and version.storage_key:
            data = get_bytes(version.storage_key)
        if data is None:
            raise RuntimeError(f"Файл шаблона {tmpl.name} недоступен")
        cache_file.write_bytes(data)
        return filename, data

    source_key = version.storage_key
    if not source_key:
        raise RuntimeError(f"У шаблона {tmpl.name} нет исходного файла")

    source_data = get_bytes(source_key)
    source_suffix = Path(source_name).suffix.lower()
    template_text = template_service.cached_version_source_text(version)

    context = _build_context(
        recipient,
        campaign,
        template_id=template_id,
        template_name=str(tmpl.name or ""),
        template_text=template_text,
        allocate_document_id=True,
    )

    if source_suffix == ".docx":
        text = template_text
        with TemporaryDirectory(prefix="template-render-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / Path(source_name).name
            output_docx = root / "rendered.docx"
            output_pdf = root / "rendered.pdf"
            source_path.write_bytes(source_data)
            replacements = _build_replacements(context, text, source_path=source_path)
            render_docx(source_path, replacements, output_docx, context)
            rendered_text = template_service._file_text(  # noqa: SLF001
                output_docx.name,
                output_docx.read_bytes(),
            )
            from src.campaigns.substitution_engine import find_unresolved_placeholders

            unresolved = find_unresolved_placeholders(rendered_text)
            if unresolved:
                raise ValueError(
                    "Не заполнены переменные во вложении: " + ", ".join(unresolved)
                )
            if not wants_pdf:
                rendered_data = output_docx.read_bytes()
                cache_file.write_bytes(rendered_data)
                return delivery_name, rendered_data
            file_kind = "kp" if str(tmpl.template_type or "").strip().lower() in {"kp", "document"} else None
            from src.generator.generation.pdf_safe import is_kp_docx

            if is_kp_docx(source_path):
                file_kind = "kp"
            from src.campaigns.font_service import font_conversion_environment

            with font_conversion_environment(tmpl.owner_username, source_data) as font_environment:
                active_font_pack_hash = (
                    font_environment.font_pack_hash
                    if font_environment
                    else expected_font_pack_hash
                )
                fontconfig_path = font_environment.fontconfig_path if font_environment else None
                prefer_local = font_environment is not None
                if file_kind == "kp":
                    _convert_kp_docx_to_pdf(
                        output_docx,
                        pdf_cache,
                        file_kind=file_kind,
                        company=str(recipient.company or ""),
                        fontconfig_path=fontconfig_path,
                        prefer_local=prefer_local,
                        font_pack_hash=active_font_pack_hash,
                        template_version_id=str(version.id),
                    )
                else:
                    _convert_docx_to_pdf(
                        output_docx,
                        pdf_cache,
                        file_kind=file_kind,
                        fontconfig_path=fontconfig_path,
                        prefer_local=prefer_local,
                    )
                    _write_render_meta(
                        pdf_cache,
                        font_pack_hash=active_font_pack_hash,
                        template_version_id=str(version.id),
                    )
            return delivery_name, pdf_cache.read_bytes()

    if source_suffix == ".pdf":
        editor_state = version.editor_state if isinstance(version.editor_state, dict) else None
        if editor_state and editor_state.get("fields"):
            pdf_data = _render_pdf_overlay(source_data, editor_state, context)
        else:
            from src.campaigns.pdf_overlay_service import render_pdf_with_discovered_placeholders

            placeholders = discover_placeholders(template_text)
            if placeholders:
                pdf_data = render_pdf_with_discovered_placeholders(source_data, placeholders, context)
            else:
                pdf_data = source_data
        pdf_cache.write_bytes(pdf_data)
        _write_render_meta(pdf_cache, template_version_id=str(version.id))
        return delivery_name, pdf_data

    if source_suffix in {".html", ".htm"}:
        rendered_html = render_template_text(
            source_data.decode("utf-8", errors="replace"),
            recipient=recipient,
            campaign=campaign,
            template_id=template_id,
            template_name=str(tmpl.name or ""),
            template_text=template_text,
            allocate_document_id=True,
        )
        from src.campaigns.substitution_engine import find_unresolved_placeholders

        unresolved = find_unresolved_placeholders(rendered_html)
        if unresolved:
            raise ValueError(
                "Не заполнены переменные во вложении: " + ", ".join(unresolved)
            )
        rendered_data = rendered_html.encode("utf-8")
        if wants_pdf:
            pdf_data, _pdf_name = template_service._build_document_pdf_artifact(  # noqa: SLF001
                source_name,
                rendered_data,
                owner_username=tmpl.owner_username,
            )
            pdf_cache.write_bytes(pdf_data)
            return delivery_name, pdf_data
        cache_file.write_bytes(rendered_data)
        return delivery_name, rendered_data

    cache_file.write_bytes(source_data)
    return delivery_name, source_data


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

    wants_pdf = str(tmpl.attachment_output_format or "original") == "pdf"
    source_suffix = Path(str(version.filename or "document")).suffix.lower() or ".pdf"
    expected_font_pack_hash = ""
    if source_suffix == ".docx":
        from src.campaigns.font_service import template_font_pack_hash

        expected_font_pack_hash = template_font_pack_hash(template_id, owner_username)

    if tmpl.is_template and campaign is not None and recipient is not None and job_id:
        delivery_name = _resolve_delivery_filename(
            tmpl,
            version,
            source_name=str(version.filename or tmpl.name),
        )
        target_cache = _cache_path(
            job_id,
            int(recipient.id),
            template_id,
            ".pdf" if wants_pdf else source_suffix,
        )
        if target_cache.exists() and (
            target_cache.suffix.lower() != ".pdf"
            or _cached_pdf_is_valid(
                target_cache,
                expected_font_pack_hash=expected_font_pack_hash,
                expected_template_version_id=str(version.id),
                require_meta=True,
            )
        ):
            return delivery_name, target_cache.read_bytes()
        try:
            filename, data = render_document_template_for_recipient(
                template_id=template_id,
                recipient=recipient,
                campaign=campaign,
                job_id=job_id,
            )
            return filename, data
        except Exception as exc:
            from src.campaigns.pdf_overlay_service import PdfOverlayLayoutError
            from src.generator.generation.kp_one_page_fitter import KpLayoutError

            if isinstance(exc, (KpLayoutError, PdfOverlayLayoutError)):
                raise
            pass

    if job_id:
        delivery_name = _resolve_delivery_filename(
            tmpl,
            version,
            source_name=str(version.filename or tmpl.name),
        )
        suffixes = (".pdf",) if wants_pdf else (source_suffix,)
        for suffix in suffixes:
            cache_file = _cache_dir(job_id, recipient_id) / f"{template_id}{suffix}"
            if not cache_file.exists():
                continue
            if suffix == ".pdf" and tmpl.is_template and not _cached_pdf_is_valid(
                cache_file,
                expected_font_pack_hash=expected_font_pack_hash,
                expected_template_version_id=str(version.id),
                require_meta=True,
            ):
                continue
            return delivery_name, cache_file.read_bytes()

    filename = _resolve_delivery_filename(
        tmpl,
        version,
        source_name=str(version.filename or tmpl.name),
    )
    data: bytes | None = None
    source_suffix = Path(str(version.filename or "")).suffix.lower()
    if wants_pdf and source_suffix == ".docx":
        item = template_service.get_template_delivery_file(
            template_id,
            owner_username,
        )
        if item is not None:
            return str(item["filename"]), bytes(item["content"])
    if wants_pdf and version.rendered_pdf_storage_key:
        try:
            data = get_bytes(version.rendered_pdf_storage_key)
        except Exception:
            data = None
    if wants_pdf and data is None:
        item = template_service.get_template_delivery_file(
            template_id,
            owner_username,
        )
        if item is not None:
            return str(item["filename"]), bytes(item["content"])
    if not wants_pdf and version.storage_key:
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
    allocate_document_id: bool = True,
) -> str:
    if not should_render_email_template(template_id):
        return text
    template_name = ""
    template_text = text
    if template_id:
        template_name, loaded_text = _template_render_metadata(str(template_id))
        if loaded_text:
            template_text = loaded_text
    return render_template_text(
        text,
        recipient=recipient,
        campaign=campaign,
        template_id=template_id,
        template_name=template_name,
        template_text=template_text,
        allocate_document_id=allocate_document_id,
    )


def load_manifest(job_id: str) -> dict[str, Any]:
    result = read_json(_manifest_path(job_id), default={})
    return result.data if result.ok and isinstance(result.data, dict) else {}
