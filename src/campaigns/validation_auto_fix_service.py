"""Orchestrated AI-assisted fixes for campaign validation issues."""

from __future__ import annotations

import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from src.campaigns import template_service
from src.campaigns.service import validate_campaign_for_launch
from src.campaigns.substitution_ai import normalize_placeholders
from src.campaigns.substitution_engine import (
    discover_brace_artifacts,
    discover_malformed_placeholders,
    find_template_defects,
    html_to_review_text,
    is_resolvable_placeholder_fragment,
)
from src.campaigns.variable_match_service import (
    _collect_templates_for_validation,
    auto_resolve_artifact_mappings,
    save_artifact_mappings,
    save_variable_mapping,
    suggest_variable_mapping,
    substitution_validation_issues,
)
from src.infra.db import session_scope
from src.infra.models import Campaign
from src.security.company_access import can_access_owner

_SAFE_TEXT_KINDS = frozenset({"punctuation", "grammar", "case"})
_AI_TEXT_KINDS = frozenset({"grammar", "punctuation", "case", "artifact", "malformed", "unresolved"})
_PLACEHOLDER_ISSUE_KINDS = frozenset({"artifact", "malformed", "unresolved"})


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _canonical_brace(canonical: str) -> str:
    return f"{{{{{canonical}}}}}"


def _template_version_fields(tmpl: dict[str, Any]) -> tuple[str, str, str]:
    version = tmpl.get("version") if isinstance(tmpl.get("version"), dict) else {}
    subject = str(version.get("subject") or tmpl.get("subject") or "")
    body_html = str(version.get("body_html") or tmpl.get("body_html") or "")
    body_text = str(version.get("body_text") or tmpl.get("body_text") or "")
    return subject, body_html, body_text


def _issue_fragment(raw: Any) -> str:
    fragment = str(raw or "")
    return fragment


def _resolve_canonical_placeholder(name: str) -> str | None:
    from src.campaigns.placeholder_semantic import resolve_recipient_canonical, resolve_system_canonical

    clean = str(name or "").strip()
    if not clean:
        return None
    canonical = resolve_system_canonical(clean) or resolve_recipient_canonical(clean)
    if canonical:
        return canonical
    if clean.isupper() and re.fullmatch(r"[A-Z0-9_]+", clean):
        return clean
    return None


def _is_mapping_resolvable_artifact(fragment: str) -> bool:
    return is_resolvable_placeholder_fragment(fragment, "artifact")


def _replace_in_field(field_text: str, fragment: str, replacement: str) -> tuple[str, bool]:
    if not fragment or fragment == replacement:
        return field_text, False
    if fragment in field_text:
        return field_text.replace(fragment, replacement, 1), True

    norm_fragment = _normalize_ws(fragment)
    if not norm_fragment:
        return field_text, False

    plain = html_to_review_text(field_text) if "<" in field_text and ">" in field_text else field_text
    if norm_fragment in _normalize_ws(plain):
        words = norm_fragment.split()
        if words:
            pattern_parts = [re.escape(word) for word in words]
            pattern = r"\s*(?:<[^>]+>\s*)*".join(pattern_parts)
            match = re.search(pattern, field_text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                return field_text[: match.start()] + replacement + field_text[match.end() :], True

    flex_pattern = re.sub(r"\\ ", r"\\s+", re.escape(fragment))
    match = re.search(flex_pattern, field_text)
    if match:
        return field_text[: match.start()] + replacement + field_text[match.end() :], True

    return field_text, False


def _source_excerpt(field_text: str, fragment: str, *, radius: int = 100) -> str:
    if fragment and fragment in field_text:
        index = field_text.index(fragment)
        start = max(0, index - radius)
        end = min(len(field_text), index + len(fragment) + radius)
        return field_text[start:end]
    plain = html_to_review_text(field_text) if "<" in field_text and ">" in field_text else field_text
    norm_fragment = _normalize_ws(fragment)
    norm_plain = _normalize_ws(plain)
    if norm_fragment and norm_fragment in norm_plain:
        index = norm_plain.index(norm_fragment)
        start = max(0, index - radius)
        end = min(len(norm_plain), index + len(norm_fragment) + radius)
        return norm_plain[start:end]
    return plain[: radius * 2]


def _apply_token_replacements(text: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    updated = text
    applied = 0
    for old, new in replacements:
        if not old or not new or old == new:
            continue
        next_text, ok = _replace_in_field(updated, old, new)
        if not ok:
            continue
        updated = next_text
        applied += 1
    return updated, applied


def _apply_placeholder_normalization(
    template_id: str,
    owner_username: str,
    *,
    subject: str,
    body_html: str,
    body_text: str,
) -> list[dict[str, str]]:
    applied: list[dict[str, str]] = []
    fields = {
        "subject": subject or "",
        "body_html": body_html or "",
        "body_text": body_text or "",
    }
    next_fields = dict(fields)
    for field_name, field_text in fields.items():
        if not field_text.strip():
            continue
        replacements: list[tuple[str, str]] = []
        for item in normalize_placeholders(field_text):
            token = str(item.get("token") or "").strip()
            canonical = str(item.get("name") or "").strip()
            if not token or not canonical or token == canonical:
                continue
            if "{{" in token or "}}" in token:
                replacement = _canonical_brace(canonical)
            else:
                replacement = canonical
            replacements.append((token, replacement))

        updated, count = _apply_token_replacements(field_text, replacements)
        if count:
            next_fields[field_name] = updated
            applied.append(
                {
                    "kind": "placeholder",
                    "message": f"Нормализованы плейсхолдеры в поле {field_name} ({count})",
                }
            )

    if applied:
        template_service.save_version(
            template_id,
            owner_username,
            subject=next_fields["subject"],
            body_html=next_fields["body_html"],
            body_text=next_fields["body_text"],
        )
    return applied


def _placeholder_replacement_for_issue(issue: dict[str, Any]) -> str | None:
    kind = str(issue.get("kind") or "")
    fragment = str(issue.get("fragment") or "").strip()
    if not fragment:
        return None

    if kind == "malformed":
        for item in discover_malformed_placeholders(fragment):
            canonical = _resolve_canonical_placeholder(item.name)
            if canonical:
                return _canonical_brace(canonical)
        return None

    if kind in {"artifact", "unresolved"}:
        if fragment.startswith("{{"):
            inner = fragment.strip("{}").strip()
            canonical = _resolve_canonical_placeholder(inner)
            if canonical:
                return _canonical_brace(canonical)
            return None
        if fragment.startswith("{"):
            inner = fragment[1:].strip()
            first_token = inner.split()[0] if inner else ""
            canonical = _resolve_canonical_placeholder(first_token)
            if canonical:
                return _canonical_brace(canonical)
            return None
        canonical = _resolve_canonical_placeholder(fragment)
        if canonical:
            return _canonical_brace(canonical)
    return None


def _apply_placeholder_defect_fixes(
    template_id: str,
    owner_username: str,
    issues: list[dict[str, Any]],
    *,
    subject: str,
    body_html: str,
    body_text: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    applied: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    fields = {
        "subject": subject or "",
        "body_html": body_html or "",
        "body_text": body_text or "",
    }
    next_fields = dict(fields)

    for field_name, field_text in fields.items():
        if not field_text.strip():
            continue
        replacements: list[tuple[str, str]] = []
        for defect in find_template_defects(field_text, source="original"):
            canonical = _resolve_canonical_placeholder(defect.name)
            if canonical:
                replacements.append((defect.token, _canonical_brace(canonical)))
        for item in discover_malformed_placeholders(field_text):
            canonical = _resolve_canonical_placeholder(item.name)
            if canonical:
                replacements.append((item.token, _canonical_brace(canonical)))

        updated, count = _apply_token_replacements(field_text, replacements)
        if count:
            next_fields[field_name] = updated
            applied.append(
                {
                    "kind": "placeholder",
                    "message": f"Исправлены дефекты плейсхолдеров в поле {field_name} ({count})",
                }
            )

    seen_issue_keys: set[tuple[str, str, str]] = set()
    for issue in issues:
        kind = str(issue.get("kind") or "")
        if kind not in _PLACEHOLDER_ISSUE_KINDS:
            continue
        fragment = _issue_fragment(issue.get("fragment"))
        field = str(issue.get("field") or "body_html")
        if not fragment or field not in next_fields:
            continue
        if kind == "artifact" and _is_mapping_resolvable_artifact(fragment):
            continue
        key = (field, kind, fragment)
        if key in seen_issue_keys:
            continue
        seen_issue_keys.add(key)

        replacement = _placeholder_replacement_for_issue(issue)
        if not replacement:
            skipped.append(
                {
                    "kind": "placeholder",
                    "message": f"Не удалось определить замену для: {fragment[:80]}",
                }
            )
            continue

        updated, ok = _replace_in_field(next_fields[field], fragment, replacement)
        if ok:
            next_fields[field] = updated
            applied.append(
                {
                    "kind": "placeholder",
                    "message": f"Исправлен плейсхолдер в {field}: {fragment} → {replacement}",
                }
            )
        else:
            skipped.append(
                {
                    "kind": "placeholder",
                    "message": f"Не найден фрагмент в шаблоне: {fragment[:80]}",
                }
            )

    if applied:
        template_service.save_version(
            template_id,
            owner_username,
            subject=next_fields["subject"],
            body_html=next_fields["body_html"],
            body_text=next_fields["body_text"],
        )
    return applied, skipped


def _apply_issue_suggestions(
    template_id: str,
    owner_username: str,
    issues: list[dict[str, Any]],
    *,
    subject: str,
    body_html: str,
    body_text: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    applied: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    fields = {
        "subject": subject or "",
        "body_html": body_html or "",
        "body_text": body_text or "",
    }
    next_fields = dict(fields)

    for issue in issues:
        kind = str(issue.get("kind") or "")
        if kind not in _SAFE_TEXT_KINDS:
            continue
        suggestion = str(issue.get("suggestion") or "")
        fragment = _issue_fragment(issue.get("fragment"))
        field = str(issue.get("field") or "body_html")
        if not suggestion.strip() or not fragment or field not in next_fields:
            continue
        updated, ok = _replace_in_field(next_fields[field], fragment, suggestion)
        if ok:
            next_fields[field] = updated
            applied.append(
                {
                    "kind": "text",
                    "message": f"Исправлено в {field}: {fragment} → {suggestion}",
                }
            )
        else:
            skipped.append(
                {
                    "kind": "text",
                    "message": f"Не найден фрагмент в шаблоне: {fragment[:80]}",
                }
            )

    if applied:
        template_service.save_version(
            template_id,
            owner_username,
            subject=next_fields["subject"],
            body_html=next_fields["body_html"],
            body_text=next_fields["body_text"],
        )
    return applied, skipped


def _apply_ai_text_rewrites(
    template_id: str,
    owner_username: str,
    issues: list[dict[str, Any]],
    *,
    subject: str,
    body_html: str,
    body_text: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    from src.campaigns.substitution_ai import _call_llm, default_model

    pending = [
        issue
        for issue in issues
        if str(issue.get("kind") or "") in _AI_TEXT_KINDS
        and not str(issue.get("suggestion") or "").strip()
        and _issue_fragment(issue.get("fragment"))
        and str(issue.get("field") or "") in {"subject", "body_html", "body_text"}
    ]
    applied: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    if not pending:
        return applied, skipped

    fields = {
        "subject": subject or "",
        "body_html": body_html or "",
        "body_text": body_text or "",
    }
    next_fields = dict(fields)

    prompt = (
        "Ты исправляешь фрагменты email-шаблона. "
        'Верни только JSON: {"fixes":[{"field":"subject|body_html|body_text","fragment":"...","replacement":"..."}]} '
        "replacement — только исправленный фрагмент, без пояснений. "
        "Для kind=artifact или kind=malformed replacement должен быть каноническим плейсхолдером вида {{WORK_TITLE}}."
    )
    lines = []
    for issue in pending[:12]:
        field = str(issue.get("field") or "body_html")
        excerpt = _source_excerpt(fields.get(field, ""), str(issue.get("fragment") or ""))
        lines.append(
            f"- field={field}; kind={issue.get('kind')}; fragment={issue.get('fragment')}; "
            f"message={issue.get('message')}; source_excerpt={excerpt}"
        )
    try:
        payload = _call_llm(default_model(), prompt, "Issues:\n" + "\n".join(lines))
    except RuntimeError:
        for issue in pending[:12]:
            skipped.append(
                {
                    "kind": "ai_text",
                    "message": f"LLM недоступен для: {str(issue.get('fragment') or '')[:80]}",
                }
            )
        return applied, skipped

    matched_issue_keys: set[tuple[str, str]] = set()
    for row in payload.get("fixes") or []:
        if not isinstance(row, dict):
            continue
        field = str(row.get("field") or "body_html")
        fragment = _issue_fragment(row.get("fragment"))
        replacement = str(row.get("replacement") or "")
        if field not in next_fields or not fragment or not replacement.strip():
            continue
        updated, ok = _replace_in_field(next_fields[field], fragment, replacement)
        if ok:
            next_fields[field] = updated
            applied.append({"kind": "ai_text", "message": f"AI: {fragment} → {replacement}"})
            matched_issue_keys.add((field, fragment))
        else:
            skipped.append(
                {
                    "kind": "ai_text",
                    "message": f"Не найден фрагмент в шаблоне: {fragment[:80]}",
                }
            )

    for issue in pending[:12]:
        field = str(issue.get("field") or "body_html")
        fragment = _issue_fragment(issue.get("fragment"))
        if not fragment or (field, fragment) in matched_issue_keys:
            continue
        skipped.append(
            {
                "kind": "ai_text",
                "message": f"Не удалось применить исправление: {fragment[:80]}",
            }
        )

    if applied:
        template_service.save_version(
            template_id,
            owner_username,
            subject=next_fields["subject"],
            body_html=next_fields["body_html"],
            body_text=next_fields["body_text"],
        )
    return applied, skipped


def _collect_document_template_ids(campaign: Campaign) -> list[str]:
    from src.campaigns.template_render_service import collect_campaign_template_ids

    chain_email_ids, chain_document_ids = collect_campaign_template_ids(campaign)
    if chain_email_ids or chain_document_ids:
        return chain_document_ids

    document_ids: list[str] = []
    document_mode = str(campaign.document_mode or "kp").lower()
    if document_mode in {"kp", "both"} and campaign.kp_template_id:
        document_ids.append(str(campaign.kp_template_id))
    if document_mode in {"contract", "both"} and campaign.contract_template_id:
        contract_id = str(campaign.contract_template_id)
        if contract_id not in document_ids:
            document_ids.append(contract_id)
    return document_ids


def _apply_philologist_docx_fixes(
    template_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    from src.generator.philologist.document_review_agent import review_docx
    from src.generator.philologist.philologist_agent import _auto_fix_docx
    from src.generator.philologist.philologist_tools import PhilologistToolRunner

    applied: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    tmpl = template_service.get_template(template_id, owner_username, visible_owners=visible_owners)
    if not tmpl:
        return applied, skipped

    file_item = template_service.get_template_file(template_id, owner_username)
    if not file_item:
        skipped.append(
            {
                "kind": "document",
                "message": f"Файл шаблона «{tmpl.get('name')}» не найден",
            }
        )
        return applied, skipped

    filename = str(file_item.get("filename") or f"{tmpl.get('name') or template_id}.docx")
    suffix = Path(filename).suffix.lower()
    template_name = str(tmpl.get("name") or template_id)

    if suffix == ".pdf":
        skipped.append(
            {
                "kind": "document",
                "message": f"Автоисправление PDF не поддерживается: «{template_name}»",
            }
        )
        return applied, skipped

    if suffix != ".docx":
        skipped.append(
            {
                "kind": "document",
                "message": f"Автоисправление формата {suffix or 'файла'} не поддерживается: «{template_name}»",
            }
        )
        return applied, skipped

    try:
        with TemporaryDirectory(prefix="campaign-doc-fix-") as temp_dir:
            docx_path = Path(temp_dir) / Path(filename).name
            docx_path.write_bytes(file_item["content"])
            review_result = review_docx(docx_path, ai_enabled=True)
            if not review_result.get("issues"):
                return applied, skipped

            fix_result = _auto_fix_docx(
                docx_path,
                review_result,
                client=None,
                tool_runner=PhilologistToolRunner(),
            )
            applied_count = int(fix_result.get("applied_fix_count") or 0)
            if applied_count > 0:
                template_service.save_docx_editor_version(
                    template_id,
                    owner_username,
                    docx_path.read_bytes(),
                )
                applied.append(
                    {
                        "kind": "document",
                        "message": f"Philologist: исправлено {applied_count} замечаний в «{template_name}»",
                    }
                )
            else:
                skipped.append(
                    {
                        "kind": "document",
                        "message": f"Не удалось автоматически исправить «{template_name}»",
                    }
                )
    except Exception as exc:
        skipped.append({"kind": "document", "message": f"«{template_name}»: {exc}"})

    return applied, skipped


def auto_fix_campaign_validation(
    campaign_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    applied: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise PermissionError("Campaign not found")

    try:
        suggest = suggest_variable_mapping(campaign_id, owner_username, visible_owners=visible_owners)
        if suggest.get("status") == "complete":
            suggested_mapping = dict(suggest.get("suggested_mapping") or {})
            if suggested_mapping:
                save_variable_mapping(
                    campaign_id,
                    owner_username,
                    suggested_mapping,
                    visible_owners=visible_owners,
                )
            applied.append({"kind": "mapping", "message": "Сопоставление переменных сохранено автоматически"})
        elif suggest.get("unmapped"):
            skipped.append(
                {
                    "kind": "mapping",
                    "message": "Не удалось автоматически сопоставить все переменные",
                }
            )
    except Exception as exc:
        skipped.append({"kind": "mapping", "message": str(exc)})

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None:
            raise PermissionError("Campaign not found")
        artifact_mappings = auto_resolve_artifact_mappings(camp)
        if artifact_mappings:
            merged = save_artifact_mappings(
                campaign_id,
                owner_username,
                artifact_mappings,
                visible_owners=visible_owners,
            )
            applied.append(
                {
                    "kind": "mapping",
                    "message": f"Сохранены резолвы артефактов ({len(merged)})",
                }
            )

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None:
            raise PermissionError("Campaign not found")
        template_items = _collect_templates_for_validation(camp)

    for item in template_items:
        template_id = str(item.get("template_id") or "").strip()
        if not template_id:
            continue
        subject = str(item.get("subject") or "")
        body_html = str(item.get("body_html") or "")
        body_text = str(item.get("body_text") or "")
        applied.extend(
            _apply_placeholder_normalization(
                template_id,
                owner_username,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
            )
        )

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None:
            raise PermissionError("Campaign not found")
        issues = substitution_validation_issues(
            camp,
            deep=True,
            include_placeholder_issues=True,
            strict_preview=True,
        )

    issues_by_template: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        template_id = str(issue.get("template_id") or "").strip()
        if template_id:
            issues_by_template.setdefault(template_id, []).append(issue)

    for item in template_items:
        template_id = str(item.get("template_id") or "").strip()
        if not template_id:
            continue
        tmpl = template_service.get_template(template_id, owner_username, visible_owners=visible_owners)
        if not tmpl:
            continue
        subject, body_html, body_text = _template_version_fields(tmpl)
        template_issues = issues_by_template.get(template_id, [])

        defect_applied, defect_skipped = _apply_placeholder_defect_fixes(
            template_id,
            owner_username,
            template_issues,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )
        applied.extend(defect_applied)
        skipped.extend(defect_skipped)

        if defect_applied:
            tmpl = template_service.get_template(template_id, owner_username, visible_owners=visible_owners)
            if tmpl:
                subject, body_html, body_text = _template_version_fields(tmpl)

        sugg_applied, sugg_skipped = _apply_issue_suggestions(
            template_id,
            owner_username,
            template_issues,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )
        applied.extend(sugg_applied)
        skipped.extend(sugg_skipped)

        if sugg_applied:
            tmpl = template_service.get_template(template_id, owner_username, visible_owners=visible_owners)
            if tmpl:
                subject, body_html, body_text = _template_version_fields(tmpl)

        ai_applied, ai_skipped = _apply_ai_text_rewrites(
            template_id,
            owner_username,
            template_issues,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )
        applied.extend(ai_applied)
        skipped.extend(ai_skipped)

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None:
            raise PermissionError("Campaign not found")
        document_template_ids = _collect_document_template_ids(camp)

    for document_template_id in document_template_ids:
        doc_applied, doc_skipped = _apply_philologist_docx_fixes(
            document_template_id,
            owner_username,
            visible_owners=visible_owners,
        )
        applied.extend(doc_applied)
        skipped.extend(doc_skipped)

    validation = validate_campaign_for_launch(
        campaign_id,
        owner_username,
        visible_owners=visible_owners,
        deep=True,
    )
    if validation.get("errors"):
        for message in validation["errors"]:
            if any(
                token in str(message).lower()
                for token in ("назван", "подключ", "получател", "цепоч", "блок", "root_node")
            ):
                skipped.append({"kind": "manual", "message": str(message)})

    return {
        "applied": applied,
        "skipped": skipped,
        "validation": validation,
    }
