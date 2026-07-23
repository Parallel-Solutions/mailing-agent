"""Variable mapping between campaign templates and recipient columns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.campaigns import template_service
from src.campaigns.service import CORE_RECIPIENT_COLUMNS, extract_recipient_columns
from src.campaigns.variable_match_ai import default_model, suggest_mappings_with_ai
from src.generator.generation.template_analysis import _mapping_suggestions, _norm_token
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient, MailTemplate, TemplateVersion
from src.infra.object_store import get_bytes
from src.security.company_access import can_access_owner

USER_INPUT_VARIABLES = frozenset(
    {
        "campaign_name",
        "DATE",
        "current_date",
        "CURRENT_DATE",
        "OUTGOING_NUMBER",
        "DOCUMENT_ID",
        "WORK_TITLE",
        "PRICE_TOTAL",
        "VALID_UNTIL",
        "DIRECTOR_NAME",
        "MUN_R_SCOPE_FRAGMENT",
    }
)

EMAIL_CORE_DEFAULTS: dict[str, str] = {
    "company": "company",
    "contact_name": "contact_name",
    "email": "email",
    "region": "region",
}

CONFIDENCE_THRESHOLD = 0.85
LITERAL_PREFIX = "="


def _is_literal_value(value: str) -> bool:
    return str(value or "").startswith(LITERAL_PREFIX)


def _literal_text(value: str) -> str:
    return str(value or "")[len(LITERAL_PREFIX) :]


def _normalize_mapping_value(raw: str, allowed_columns: set[str]) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if _is_literal_value(text):
        return text
    lowered = text.lower()
    if lowered in allowed_columns:
        return lowered
    return f"{LITERAL_PREFIX}{text}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_recipient_facing(name: str, source: str | None = None) -> bool:
    if name in USER_INPUT_VARIABLES:
        return False
    if source == "artifact":
        from src.campaigns.placeholder_semantic import resolve_recipient_canonical, resolve_system_canonical

        if resolve_system_canonical(name):
            return False
        if resolve_recipient_canonical(name):
            return True
        return False
    if source in {"recipient", "pdf"}:
        return True
    if name in EMAIL_CORE_DEFAULTS:
        return True
    if name.isupper() or ("_" in name and name.upper() == name):
        return True
    return source != "user_input"


def _load_template_version(session: Any, template_id: str | None) -> TemplateVersion | None:
    if not template_id:
        return None
    tmpl = session.get(MailTemplate, template_id)
    if tmpl is None or not tmpl.active_version_id:
        return None
    return session.get(TemplateVersion, tmpl.active_version_id)


def _merge_variables(
    variables: dict[str, dict[str, Any]],
    items: list[dict[str, Any]],
) -> None:
    for item in items:
        name = str(item.get("name") or "").strip()
        if name:
            variables[name] = {
                "name": name,
                "label": str(item.get("label") or name),
                "source": str(item.get("source") or "recipient"),
            }


def _merge_email_template_variables(
    session: Any,
    variables: dict[str, dict[str, Any]],
    template_id: str | None,
) -> None:
    from src.campaigns.substitution_engine import discover_brace_artifacts

    email_version = _load_template_version(session, template_id)
    if email_version:
        combined = (email_version.subject or "") + "\n" + (email_version.body_html or "")
        _merge_variables(variables, [item for item in (email_version.variables or []) if isinstance(item, dict)])
        for item in template_service._extract_variables(combined):  # noqa: SLF001
            name = str(item.get("name") or "").strip()
            if name and name not in variables:
                variables[name] = item
        for item in discover_brace_artifacts(combined):
            name = str(item.name or "").strip()
            if name and name not in variables:
                variables[name] = {
                    "name": name,
                    "label": name,
                    "source": "artifact",
                }


def _merge_document_template_variables(
    session: Any,
    variables: dict[str, dict[str, Any]],
    template_id: str | None,
) -> None:
    version = _load_template_version(session, template_id)
    if version is None:
        return
    _merge_variables(variables, [item for item in (version.variables or []) if isinstance(item, dict)])
    text = version.body_html or ""
    if version.storage_key and version.filename:
        try:
            text = template_service._file_text(  # noqa: SLF001
                version.filename,
                get_bytes(version.storage_key),
            )
        except Exception:
            text = text or ""
    for item in template_service._extract_variables(text):  # noqa: SLF001
        name = str(item.get("name") or "").strip()
        if name and name not in variables:
            variables[name] = item


def _chain_template_ids(draft: dict[str, Any]) -> tuple[list[str], list[str]]:
    chain = draft.get("email_chain")
    if not isinstance(chain, dict):
        return [], []
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


def collect_template_variables(campaign: Campaign) -> list[dict[str, Any]]:
    from src.campaigns.template_render_service import collect_campaign_template_ids

    variables: dict[str, dict[str, Any]] = {}
    draft = dict(campaign.draft_payload or {})
    chain_email_ids, chain_document_ids = collect_campaign_template_ids(campaign)

    with session_scope() as session:
        if chain_email_ids or chain_document_ids:
            for template_id in chain_email_ids:
                _merge_email_template_variables(session, variables, template_id)
            for template_id in chain_document_ids:
                _merge_document_template_variables(session, variables, template_id)
        else:
            _merge_email_template_variables(session, variables, campaign.email_template_id)
            if not variables and draft.get("email_body"):
                for item in template_service._extract_variables(str(draft.get("email_body") or "")):  # noqa: SLF001
                    name = str(item.get("name") or "").strip()
                    if name:
                        variables[name] = item

            document_mode = str(campaign.document_mode or "kp").lower()
            template_ids: list[str] = []
            if document_mode in {"kp", "both"} and campaign.kp_template_id:
                template_ids.append(campaign.kp_template_id)
            if document_mode in {"contract", "both"} and campaign.contract_template_id:
                if campaign.contract_template_id not in template_ids:
                    template_ids.append(campaign.contract_template_id)

            for template_id in template_ids:
                _merge_document_template_variables(session, variables, template_id)

    result = [
        item
        for item in variables.values()
        if _is_recipient_facing(str(item.get("name") or ""), str(item.get("source") or ""))
    ]
    return sorted(result, key=lambda item: str(item.get("name") or ""))


def collect_recipient_columns(campaign: Campaign, *, sample_limit: int = 50) -> tuple[list[str], dict[str, list[str]]]:
    draft = dict(campaign.draft_payload or {})
    columns = list(draft.get("recipient_columns") or CORE_RECIPIENT_COLUMNS)
    seen = set(columns)
    samples: dict[str, list[str]] = {col: [] for col in columns}

    with session_scope() as session:
        rows = session.scalars(
            select(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign.id)
            .order_by(CampaignRecipient.row_index)
            .limit(sample_limit)
        ).all()
        if rows and not draft.get("recipient_columns"):
            columns = extract_recipient_columns(
                [
                    {
                        "extra": dict(row.extra or {}),
                    }
                    for row in rows
                ]
            )
            seen = set(columns)
            samples = {col: [] for col in columns}

        for row in rows:
            for col in CORE_RECIPIENT_COLUMNS:
                value = str(getattr(row, col, "") or "").strip()
                if value and len(samples[col]) < 2:
                    samples[col].append(value)
            for key, value in (row.extra or {}).items():
                normalized = str(key or "").strip().lower()
                if not normalized:
                    continue
                if normalized not in seen:
                    seen.add(normalized)
                    columns.append(normalized)
                    samples[normalized] = []
                if value and len(samples[normalized]) < 2:
                    samples[normalized].append(str(value))

    return columns, samples


def _heuristic_mapping(
    template_variables: list[dict[str, Any]],
    recipient_columns: list[str],
    *,
    column_samples: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    names = [str(item.get("name") or "") for item in template_variables]
    suggestions = _mapping_suggestions(names, recipient_columns, column_samples=column_samples)
    mapping: dict[str, str] = {}

    for item in suggestions:
        placeholder = str(item.get("placeholder") or "")
        candidates = item.get("candidates") or []
        if not placeholder or not candidates:
            continue
        best = candidates[0]
        column = str(best.get("column") or "").strip().lower()
        confidence = float(best.get("confidence") or 0)
        reason = str(best.get("reason") or "")
        threshold = CONFIDENCE_THRESHOLD
        if reason == "semantic_match":
            from src.generator.generation.config_generator import RAG_SEMANTIC_MIN_SCORE

            threshold = max(RAG_SEMANTIC_MIN_SCORE, 0.75)
        if column and confidence >= threshold:
            mapping[placeholder] = column

    for name in names:
        if name in mapping:
            continue
        default = EMAIL_CORE_DEFAULTS.get(name)
        if default and default in recipient_columns:
            mapping[name] = default
            continue
        normalized_name = _norm_token(name)
        for column in recipient_columns:
            if _norm_token(column) == normalized_name:
                mapping[name] = column
                break

    return mapping


def auto_resolve_artifact_mappings(campaign: Campaign) -> dict[str, str]:
    from src.campaigns.placeholder_semantic import resolve_recipient_canonical, resolve_system_canonical
    from src.campaigns.substitution_engine import discover_brace_artifacts
    from src.generator.generation.template_analysis import _norm_token

    resolved: dict[str, str] = {}
    for item in _collect_templates_for_validation(campaign):
        text = "\n".join(
            [
                str(item.get("subject") or ""),
                str(item.get("body_html") or ""),
                str(item.get("body_text") or ""),
                str(item.get("text") or ""),
            ]
        )
        for artifact in discover_brace_artifacts(text):
            inner = str(artifact.name or "").strip()
            if not inner or inner in resolved:
                continue
            system_canonical = resolve_system_canonical(inner)
            if system_canonical:
                resolved[inner] = system_canonical
                continue
            recipient_canonical = resolve_recipient_canonical(inner)
            if recipient_canonical and _norm_token(inner) != _norm_token(recipient_canonical):
                resolved[inner] = recipient_canonical
    return resolved


def save_artifact_mappings(
    campaign_id: str,
    owner_username: str,
    mappings: dict[str, str],
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, str]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise PermissionError("Campaign not found")
        draft = dict(camp.draft_payload or {})
        current = dict(draft.get("system_variables") or {})
        changed = False
        for key, canonical in mappings.items():
            clean_key = str(key).strip()
            clean_canonical = str(canonical).strip()
            if not clean_key or not clean_canonical:
                continue
            if current.get(clean_key) != clean_canonical:
                current[clean_key] = clean_canonical
                changed = True
        if changed:
            draft["system_variables"] = current
            camp.draft_payload = draft
            session.flush()
        return current


def suggest_variable_mapping(
    campaign_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
    model: str = "",
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise PermissionError("Campaign not found")

        template_variables = collect_template_variables(camp)
        recipient_columns, column_samples = collect_recipient_columns(camp)

        if not template_variables:
            draft = dict(camp.draft_payload or {})
            draft["variable_mapping"] = {}
            draft["system_variables"] = {}
            draft["mapping_confirmed"] = True
            draft["mapping_confirmed_at"] = _now_iso()
            camp.draft_payload = draft
            session.flush()
            return {
                "status": "complete",
                "template_variables": [],
                "recipient_columns": recipient_columns,
                "suggested_mapping": {},
                "system_variables": {},
                "unmapped": [],
            }

        from src.campaigns.substitution_ai import classify_system_variables

        artifact_resolved = auto_resolve_artifact_mappings(camp)
        classification = classify_system_variables(template_variables, model=model or default_model())
        system_resolved = dict(classification.get("system_resolved") or {})
        system_resolved.update(dict(camp.draft_payload or {}).get("system_variables") or {})
        system_resolved.update(artifact_resolved)
        recipient_variables = [
            item
            for item in template_variables
            if str(item.get("name") or "") not in system_resolved
        ]

        if not recipient_variables:
            draft = dict(camp.draft_payload or {})
            draft["variable_mapping"] = {}
            draft["system_variables"] = system_resolved
            draft["mapping_confirmed"] = True
            draft["mapping_confirmed_at"] = _now_iso()
            camp.draft_payload = draft
            session.flush()
            return {
                "status": "complete",
                "template_variables": template_variables,
                "recipient_columns": recipient_columns,
                "suggested_mapping": {},
                "system_variables": system_resolved,
                "unmapped": [],
            }

        suggested = _heuristic_mapping(
            recipient_variables,
            recipient_columns,
            column_samples=column_samples,
        )
        ai_result = suggest_mappings_with_ai(
            template_variables=recipient_variables,
            recipient_columns=recipient_columns,
            column_samples=column_samples,
            already_mapped=suggested,
            model=model or default_model(),
        )
        for item in ai_result.get("mappings") or []:
            name = str(item.get("template_variable") or "")
            column = str(item.get("recipient_column") or "")
            confidence = float(item.get("confidence") or 0)
            if name and column and confidence >= 0.55:
                suggested[name] = column

        unmapped = [
            str(item.get("name") or "")
            for item in recipient_variables
            if str(item.get("name") or "") not in suggested
        ]
        status = "complete" if not unmapped else "needs_review"
        return {
            "status": status,
            "template_variables": template_variables,
            "recipient_columns": recipient_columns,
            "suggested_mapping": suggested,
            "system_variables": system_resolved,
            "unmapped": unmapped,
        }


def get_variable_mapping_state(
    campaign_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise PermissionError("Campaign not found")
        draft = dict(camp.draft_payload or {})
        template_variables = collect_template_variables(camp)
        recipient_columns, _ = collect_recipient_columns(camp)
        from src.campaigns.substitution_ai import classify_system_variables

        system_resolved = dict(draft.get("system_variables") or {})
        if not system_resolved:
            system_resolved = dict(classify_system_variables(template_variables).get("system_resolved") or {})
        system_resolved.update(auto_resolve_artifact_mappings(camp))
        recipient_variables = [
            item
            for item in template_variables
            if str(item.get("name") or "") not in system_resolved
        ]
        return {
            "mapping_confirmed": bool(draft.get("mapping_confirmed")),
            "mapping_confirmed_at": draft.get("mapping_confirmed_at"),
            "variable_mapping": dict(draft.get("variable_mapping") or {}),
            "system_variables": system_resolved,
            "recipient_columns": recipient_columns,
            "template_variables": template_variables,
            "recipient_template_variables": recipient_variables,
        }


def save_variable_mapping(
    campaign_id: str,
    owner_username: str,
    mapping: dict[str, str],
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or not can_access_owner(visible_owners, camp.owner_username):
            raise PermissionError("Campaign not found")

        template_variables = collect_template_variables(camp)
        from src.campaigns.substitution_ai import classify_system_variables

        system_resolved = dict(classify_system_variables(template_variables).get("system_resolved") or {})
        system_resolved.update(auto_resolve_artifact_mappings(camp))
        recipient_variables = [
            item
            for item in template_variables
            if str(item.get("name") or "") not in system_resolved
        ]
        recipient_columns, _ = collect_recipient_columns(camp)
        allowed_columns = set(recipient_columns)
        normalized_mapping: dict[str, str] = {}
        missing: list[str] = []

        for item in recipient_variables:
            name = str(item.get("name") or "")
            if not name:
                continue
            normalized = _normalize_mapping_value(str(mapping.get(name) or ""), allowed_columns)
            if not normalized:
                missing.append(name)
                continue
            normalized_mapping[name] = normalized

        if missing:
            raise ValueError("Не заполнены переменные: " + ", ".join(missing))

        draft = dict(camp.draft_payload or {})
        draft["variable_mapping"] = normalized_mapping
        draft["system_variables"] = system_resolved
        draft["recipient_columns"] = recipient_columns
        draft["mapping_confirmed"] = True
        draft["mapping_confirmed_at"] = _now_iso()
        camp.draft_payload = draft
        session.flush()
        return {
            "mapping_confirmed": True,
            "mapping_confirmed_at": draft["mapping_confirmed_at"],
            "variable_mapping": normalized_mapping,
        }


def mapping_validation_errors(campaign: Campaign) -> list[str]:
    template_variables = collect_template_variables(campaign)
    if not template_variables:
        return []
    draft = dict(campaign.draft_payload or {})
    if not draft.get("mapping_confirmed"):
        return ["Заполните сопоставление переменных"]
    mapping = dict(draft.get("variable_mapping") or {})
    system_resolved = dict(draft.get("system_variables") or {})
    missing = [
        str(item.get("name") or "")
        for item in template_variables
        if str(item.get("name") or "") not in mapping
        and str(item.get("name") or "") not in system_resolved
    ]
    if missing:
        return ["Не сопоставлены переменные: " + ", ".join(missing)]
    return []


def resolve_recipient_value(recipient: CampaignRecipient, column: str) -> str:
    raw = str(column or "").strip()
    if _is_literal_value(raw):
        return _literal_text(raw)
    normalized = raw.lower()
    if normalized in CORE_RECIPIENT_COLUMNS:
        return str(getattr(recipient, normalized, "") or "")
    return str((recipient.extra or {}).get(normalized) or "")


def render_template_text(
    text: str,
    *,
    recipient: CampaignRecipient,
    campaign: Campaign,
    variable_mapping: dict[str, str] | None = None,
    template_id: str | None = None,
    template_name: str = "",
    template_text: str = "",
    allocate_document_id: bool = False,
) -> str:
    if not text:
        return text
    from src.campaigns.substitution_context import build_substitution_context
    from src.campaigns.substitution_engine import render_text

    effective_template_text = template_text or text
    context = build_substitution_context(
        recipient=recipient,
        campaign=campaign,
        outgoing_number=recipient.row_index or 1,
        variable_mapping=variable_mapping,
        template_id=template_id,
        template_name=template_name,
        template_text=effective_template_text,
        allocate_document_id=allocate_document_id,
    )
    return render_text(text, context)


def _collect_templates_for_validation(campaign: Campaign) -> list[dict[str, str | None]]:
    from src.campaigns.template_render_service import collect_campaign_template_ids

    items: list[dict[str, str | None]] = []
    draft = dict(campaign.draft_payload or {})
    chain_email_ids, chain_document_ids = collect_campaign_template_ids(campaign)

    with session_scope() as session:
        if chain_email_ids or chain_document_ids:
            email_ids = chain_email_ids
            document_ids = chain_document_ids
        else:
            email_ids = [campaign.email_template_id] if campaign.email_template_id else []
            document_ids = []
            document_mode = str(campaign.document_mode or "kp").lower()
            if document_mode in {"kp", "both"} and campaign.kp_template_id:
                document_ids.append(campaign.kp_template_id)
            if document_mode in {"contract", "both"} and campaign.contract_template_id:
                if campaign.contract_template_id not in document_ids:
                    document_ids.append(campaign.contract_template_id)

        for template_id in email_ids:
            tmpl = session.get(MailTemplate, template_id) if template_id else None
            version = _load_template_version(session, template_id)
            if version is None:
                continue
            text = "\n".join([version.subject or "", version.body_html or "", version.body_text or ""])
            items.append(
                {
                    "template_id": str(template_id),
                    "template_name": str(tmpl.name if tmpl else campaign.name or "email"),
                    "text": text,
                    "subject": version.subject or "",
                    "body_html": version.body_html or "",
                    "body_text": version.body_text or "",
                }
            )

        if not email_ids and draft.get("email_body"):
            body = str(draft.get("email_body") or "")
            items.append(
                {
                    "template_id": None,
                    "template_name": str(campaign.name or "email"),
                    "text": body,
                    "subject": "",
                    "body_html": body,
                    "body_text": "",
                }
            )

        for template_id in document_ids:
            tmpl = session.get(MailTemplate, template_id) if template_id else None
            version = _load_template_version(session, template_id)
            if version is None:
                continue
            text = version.body_html or ""
            if version.storage_key and version.filename:
                try:
                    text = template_service._file_text(version.filename, get_bytes(version.storage_key))  # noqa: SLF001
                except Exception:
                    text = text or ""
            if text:
                items.append(
                    {
                        "template_id": str(template_id),
                        "template_name": str(tmpl.name if tmpl else campaign.name or "document"),
                        "text": text,
                    }
                )

    return items


def _collect_template_texts_for_validation(campaign: Campaign) -> list[str]:
    return [str(item.get("text") or "") for item in _collect_templates_for_validation(campaign) if item.get("text")]


def _first_validation_recipient(campaign: Campaign) -> CampaignRecipient | None:
    with session_scope() as session:
        recipient = session.scalar(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.excluded.is_(False),
            )
            .order_by(CampaignRecipient.row_index.asc())
            .limit(1)
        )
        if recipient is not None:
            session.expunge(recipient)
        return recipient


def substitution_validation_issues(
    campaign: Campaign,
    *,
    deep: bool = False,
    advisory: bool = False,
    include_placeholder_issues: bool = False,
    strict_preview: bool = False,
) -> list[dict[str, Any]]:
    from src.campaigns.template_text_review_service import review_campaign_templates

    return review_campaign_templates(
        campaign,
        deep=deep,
        advisory=advisory,
        include_placeholder_issues=include_placeholder_issues,
        strict_preview=strict_preview,
    )


def substitution_validation_errors(campaign: Campaign, *, deep: bool = False) -> list[str]:
    from src.campaigns.template_text_review_service import partition_review_messages

    issues = substitution_validation_issues(campaign, deep=deep)
    errors, _warnings = partition_review_messages(issues)
    return errors
