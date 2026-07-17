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

USER_INPUT_VARIABLES = frozenset(
    {
        "campaign_name",
        "DATE",
        "OUTGOING_NUMBER",
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_recipient_facing(name: str, source: str | None = None) -> bool:
    if name in USER_INPUT_VARIABLES:
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
    email_version = _load_template_version(session, template_id)
    if email_version:
        _merge_variables(variables, [item for item in (email_version.variables or []) if isinstance(item, dict)])
        for item in template_service._extract_variables(  # noqa: SLF001
            (email_version.subject or "") + "\n" + (email_version.body_html or "")
        ):
            name = str(item.get("name") or "").strip()
            if name and name not in variables:
                variables[name] = item


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
    variables: dict[str, dict[str, Any]] = {}
    draft = dict(campaign.draft_payload or {})
    chain_email_ids, chain_document_ids = _chain_template_ids(draft)

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
) -> dict[str, str]:
    names = [str(item.get("name") or "") for item in template_variables]
    suggestions = _mapping_suggestions(names, recipient_columns)
    mapping: dict[str, str] = {}

    for item in suggestions:
        placeholder = str(item.get("placeholder") or "")
        candidates = item.get("candidates") or []
        if not placeholder or not candidates:
            continue
        best = candidates[0]
        column = str(best.get("column") or "").strip().lower()
        confidence = float(best.get("confidence") or 0)
        if column and confidence >= CONFIDENCE_THRESHOLD:
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


def suggest_variable_mapping(
    campaign_id: str,
    owner_username: str,
    *,
    is_admin: bool = False,
    model: str = "",
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")

        template_variables = collect_template_variables(camp)
        recipient_columns, column_samples = collect_recipient_columns(camp)

        if not template_variables:
            draft = dict(camp.draft_payload or {})
            draft["variable_mapping"] = {}
            draft["mapping_confirmed"] = True
            draft["mapping_confirmed_at"] = _now_iso()
            camp.draft_payload = draft
            session.flush()
            return {
                "status": "complete",
                "template_variables": [],
                "recipient_columns": recipient_columns,
                "suggested_mapping": {},
                "unmapped": [],
            }

        suggested = _heuristic_mapping(template_variables, recipient_columns)
        ai_result = suggest_mappings_with_ai(
            template_variables=template_variables,
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
            for item in template_variables
            if str(item.get("name") or "") not in suggested
        ]
        status = "complete" if not unmapped else "needs_review"
        return {
            "status": status,
            "template_variables": template_variables,
            "recipient_columns": recipient_columns,
            "suggested_mapping": suggested,
            "unmapped": unmapped,
        }


def get_variable_mapping_state(
    campaign_id: str,
    owner_username: str,
    *,
    is_admin: bool = False,
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")
        draft = dict(camp.draft_payload or {})
        template_variables = collect_template_variables(camp)
        recipient_columns, _ = collect_recipient_columns(camp)
        return {
            "mapping_confirmed": bool(draft.get("mapping_confirmed")),
            "mapping_confirmed_at": draft.get("mapping_confirmed_at"),
            "variable_mapping": dict(draft.get("variable_mapping") or {}),
            "recipient_columns": recipient_columns,
            "template_variables": template_variables,
        }


def save_variable_mapping(
    campaign_id: str,
    owner_username: str,
    mapping: dict[str, str],
    *,
    is_admin: bool = False,
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")

        template_variables = collect_template_variables(camp)
        recipient_columns, _ = collect_recipient_columns(camp)
        allowed_columns = set(recipient_columns)
        normalized_mapping: dict[str, str] = {}
        missing: list[str] = []

        for item in template_variables:
            name = str(item.get("name") or "")
            if not name:
                continue
            column = str(mapping.get(name) or "").strip().lower()
            if not column:
                missing.append(name)
                continue
            if column not in allowed_columns:
                raise ValueError(f"Неизвестная колонка для переменной {name}: {column}")
            normalized_mapping[name] = column

        if missing:
            raise ValueError("Не заполнены переменные: " + ", ".join(missing))

        draft = dict(camp.draft_payload or {})
        draft["variable_mapping"] = normalized_mapping
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
        return ["Подтвердите сопоставление переменных (кнопка «Сохранить»)"]
    mapping = dict(draft.get("variable_mapping") or {})
    missing = [
        str(item.get("name") or "")
        for item in template_variables
        if str(item.get("name") or "") not in mapping
    ]
    if missing:
        return ["Не сопоставлены переменные: " + ", ".join(missing)]
    return []


def resolve_recipient_value(recipient: CampaignRecipient, column: str) -> str:
    normalized = str(column or "").strip().lower()
    if normalized in CORE_RECIPIENT_COLUMNS:
        return str(getattr(recipient, normalized, "") or "")
    return str((recipient.extra or {}).get(normalized) or "")


def render_template_text(
    text: str,
    *,
    recipient: CampaignRecipient,
    campaign: Campaign,
    variable_mapping: dict[str, str] | None = None,
) -> str:
    if not text:
        return text
    mapping = dict(EMAIL_CORE_DEFAULTS)
    mapping.update(dict(variable_mapping or (campaign.draft_payload or {}).get("variable_mapping") or {}))

    rendered = text
    for var_name, column in mapping.items():
        value = resolve_recipient_value(recipient, column)
        rendered = rendered.replace(f"{{{{{var_name}}}}}", value)
    rendered = rendered.replace("{{campaign_name}}", campaign.name or "")
    return rendered
