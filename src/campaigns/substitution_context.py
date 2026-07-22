"""Unified substitution context for CampaignFlow templates."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.campaigns.variable_match_service import EMAIL_CORE_DEFAULTS, resolve_recipient_value
from src.generator.generation.transforms import build_document_context
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient, Company, CompanyMembership

INFLECTED_FIELDS = frozenset(
    {
        "ADM_NAME_1",
        "MUN_R_NAME_1",
        "MUN_NAME_1",
        "MUN_NAME_2",
        "MUN_NAME_3",
        "SUB_RF_1",
        "HEAD_FIO_1",
        "HEAD_FIO_2",
        "MUN_R_SCOPE_FRAGMENT",
        "WORK_SCOPE_FRAGMENT",
        "HEAD_MO_FRAGMENT",
        "WORK_TITLE_1",
    }
)

SYSTEM_VARIABLE_ALIASES: dict[str, str] = {
    "current_date": "DATE",
    "CURRENT_DATE": "DATE",
    "date": "DATE",
    "valid_until": "VALID_UNTIL",
    "VALID_UNTIL_DATE": "VALID_UNTIL",
    "outgoing_number": "OUTGOING_NUMBER",
    "contract_number": "OUTGOING_NUMBER",
    "director_name": "DIRECTOR_NAME",
    "price_total": "PRICE_TOTAL",
    "work_title": "WORK_TITLE",
    "Вид_работ": "WORK_TITLE",
    "вид_работ": "WORK_TITLE",
    "mun_name": "MUN_NAME",
    "MUN_NAME": "MUN_NAME",
    "campaign_name": "campaign_name",
    "ид": "DOCUMENT_ID",
    "id": "DOCUMENT_ID",
    "номер": "DOCUMENT_ID",
    "number": "DOCUMENT_ID",
    "docnumber": "DOCUMENT_ID",
    "documentnumber": "DOCUMENT_ID",
    "documentid": "DOCUMENT_ID",
    "identifier": "DOCUMENT_ID",
    "идентификатор": "DOCUMENT_ID",
    "ref": "DOCUMENT_ID",
    "reference": "DOCUMENT_ID",
    "regnumber": "DOCUMENT_ID",
}

SYSTEM_AUTO_VARIABLES = frozenset(
    {
        "campaign_name",
        "DATE",
        "current_date",
        "CURRENT_DATE",
        "VALID_UNTIL",
        "OUTGOING_NUMBER",
        "CONTRACT_NUMBER",
        "DIRECTOR_NAME",
        "PRICE_TOTAL",
        "WORK_TITLE",
        "WORK_TITLE_1",
        "WORK_TITLE_NOMINATIVE",
        "WORK_TYPE",
        "WORK_TYPE_LABEL",
        "WORK_SHORT_NAME",
        "WORK_RESULT_NAME",
        "MUN_R_SCOPE_FRAGMENT",
        "WORK_SCOPE_FRAGMENT",
        "HEAD_MO_FRAGMENT",
        "DOCUMENT_ID",
    }
)

DEFAULT_VALID_UNTIL_DAYS = 30


def _extra_value(extra: dict[str, Any], key: str, *aliases: str) -> Any:
    normalized = {str(name).strip().upper(): value for name, value in extra.items()}
    for candidate in (key, *aliases):
        value = normalized.get(candidate.upper())
        if value not in (None, ""):
            return value
    return ""


def recipient_row(recipient: CampaignRecipient) -> dict[str, Any]:
    from src.parser.excel_writer import COLUMNS

    extra = dict(recipient.extra or {})
    company = recipient.company or _extra_value(
        extra,
        "MUN_NAME",
        "ADM_NAME",
        "COMPANY",
        "ПОЛНОЕ НАИМЕНОВАНИЕ",
        "СОКРАЩЕННОЕ НАИМЕНОВАНИЕ",
    )
    row: dict[str, Any] = {
        key: _extra_value(extra, key)
        for _, key in COLUMNS
        if key
    }
    row.update(
        {
            "ID": str(recipient.id),
            "SUB_RF": _extra_value(extra, "SUB_RF") or recipient.region,
            "MUN_R_NAME": _extra_value(extra, "MUN_R_NAME"),
            "MUN_NAME": _extra_value(extra, "MUN_NAME") or company,
            "ADM_NAME": _extra_value(extra, "ADM_NAME") or company,
            "EMAIL": recipient.email or recipient.email_fallback,
            "EMAIL_OSN": recipient.email or recipient.email_fallback,
            "HEAD_FIO": recipient.contact_name or _extra_value(extra, "HEAD_FIO", "CONTACT", "РУКОВОДИТЕЛЬ"),
        }
    )
    for key, value in extra.items():
        technical_key = str(key).strip().upper()
        if technical_key and technical_key not in row:
            row[technical_key] = value
    return row


def _resolve_director_name(campaign: Campaign) -> str:
    draft = dict(campaign.draft_payload or {})
    draft_company_id = str(draft.get("company_id") or "").strip()
    with session_scope() as session:
        if draft_company_id:
            company = session.get(Company, draft_company_id)
            if company is not None:
                return str(company.contact_person_name or "").strip()
        membership = session.scalar(
            select(CompanyMembership)
            .where(CompanyMembership.username == campaign.owner_username)
            .order_by(CompanyMembership.created_at.asc())
            .limit(1)
        )
        if membership is None:
            return ""
        company = session.get(Company, membership.company_id)
        if company is None:
            return ""
        return str(company.contact_person_name or "").strip()


def _resolve_company_work_type_name(campaign: Campaign) -> str:
    draft = dict(campaign.draft_payload or {})
    company_id = str(draft.get("company_id") or "").strip()
    work_type_id = str(draft.get("company_work_type_id") or "").strip()
    if not company_id or not work_type_id:
        return ""
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            return ""
        raw_items = company.work_types if isinstance(company.work_types, list) else []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "").strip() == work_type_id:
                return str(item.get("name") or "").strip()
    return ""


def _valid_until_days(campaign: Campaign) -> int:
    draft = dict(campaign.draft_payload or {})
    raw = draft.get("valid_until_days")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = DEFAULT_VALID_UNTIL_DAYS
    return max(1, days)


def _stringify_context(raw: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        safe_key = str(key)
        result[safe_key] = str(value)
        upper = safe_key.upper()
        if upper != safe_key:
            result[upper] = str(value)
    return result


def _apply_document_id(
    string_context: dict[str, str],
    *,
    campaign: Campaign,
    recipient: CampaignRecipient,
    template_id: str | None,
    template_name: str,
    template_text: str,
    allocate_document_id: bool,
) -> None:
    from src.campaigns.document_number_service import (
        allocate_document_number,
        build_allocation_key,
        peek_document_number,
        resolve_campaign_company_id,
    )
    from src.campaigns.document_type_service import detect_document_type_key
    from src.campaigns.substitution_engine import template_has_identifier_placeholder

    if not template_text or not template_has_identifier_placeholder(template_text):
        return

    company_id = resolve_campaign_company_id(campaign)
    if not company_id:
        return

    document_type_key = detect_document_type_key(template_name=template_name, text=template_text)
    safe_template_id = str(template_id or template_name or "template").strip() or "template"
    allocation_key = build_allocation_key(
        campaign_id=str(campaign.id),
        recipient_id=int(recipient.id),
        template_id=safe_template_id,
    )
    if allocate_document_id:
        number = allocate_document_number(
            company_id=company_id,
            document_type_key=document_type_key,
            allocation_key=allocation_key,
        )
    else:
        number = peek_document_number(company_id=company_id, document_type_key=document_type_key)

    document_id = str(number)
    string_context["DOCUMENT_ID"] = document_id


def build_substitution_context(
    *,
    recipient: CampaignRecipient,
    campaign: Campaign,
    outgoing_number: int | str = 1,
    variable_mapping: dict[str, str] | None = None,
    template_id: str | None = None,
    template_name: str = "",
    template_text: str = "",
    allocate_document_id: bool = False,
) -> dict[str, str]:
    row = recipient_row(recipient)
    context = build_document_context(row, outgoing_number=outgoing_number, work_type=campaign.work_type or None)
    string_context = _stringify_context(context)

    now = datetime.now()
    date_value = now.strftime("%d.%m.%Y")
    valid_until = (now + timedelta(days=_valid_until_days(campaign))).strftime("%d.%m.%Y")
    outgoing = str(outgoing_number)
    director_name = _resolve_director_name(campaign)
    draft = dict(campaign.draft_payload or {})

    string_context.update(
        {
            "DATE": date_value,
            "VALID_UNTIL": valid_until,
            "OUTGOING_NUMBER": outgoing,
            "CONTRACT_NUMBER": outgoing,
            "campaign_name": campaign.name or "",
            "DIRECTOR_NAME": director_name,
            "PRICE_TOTAL": str(draft.get("price_total") or ""),
            "company": recipient.company or "",
            "contact_name": recipient.contact_name or "",
            "email": recipient.email or recipient.email_fallback or "",
            "region": recipient.region or "",
        }
    )

    from src.generator.generation.transforms import parse_fio_components

    fio_source = recipient.contact_name or str(row.get("HEAD_FIO") or "").strip()
    surname, first_name, patronymic = parse_fio_components(fio_source)
    contact_first_name = first_name or (fio_source if fio_source and not surname else "")
    string_context["CONTACT_FIRST_NAME"] = contact_first_name
    string_context["CONTACT_PATRONYMIC"] = patronymic
    string_context["CONTACT_SURNAME"] = surname
    if contact_first_name:
        string_context["Имя"] = contact_first_name
    if patronymic:
        string_context["Отчество"] = patronymic
    if surname:
        string_context["Фамилия"] = surname

    mapping = dict(EMAIL_CORE_DEFAULTS)
    mapping.update(dict(variable_mapping or draft.get("variable_mapping") or {}))
    for var_name, column in mapping.items():
        value = resolve_recipient_value(recipient, column)
        if not value:
            continue
        upper = str(var_name).upper()
        if upper in INFLECTED_FIELDS and string_context.get(upper):
            continue
        string_context[str(var_name)] = value
        string_context[upper] = value

    company_work_type_name = _resolve_company_work_type_name(campaign)
    if company_work_type_name:
        string_context["WORK_TITLE"] = company_work_type_name
        string_context["WORK_TITLE_1"] = company_work_type_name
        string_context["WORK_TITLE_NOMINATIVE"] = company_work_type_name

    _apply_document_id(
        string_context,
        campaign=campaign,
        recipient=recipient,
        template_id=template_id,
        template_name=template_name,
        template_text=template_text,
        allocate_document_id=allocate_document_id,
    )

    for alias, canonical in SYSTEM_VARIABLE_ALIASES.items():
        canonical_value = string_context.get(canonical) or string_context.get(canonical.upper())
        if canonical_value and alias not in string_context:
            string_context[alias] = canonical_value

    for template_var, canonical in (draft.get("system_variables") or {}).items():
        template_var = str(template_var).strip()
        canonical = str(canonical).strip()
        if not template_var or not canonical:
            continue
        value = string_context.get(canonical) or string_context.get(canonical.upper())
        if not value:
            continue
        string_context[template_var] = value
        brace_token = f"{{{{{template_var}}}}}"
        string_context[brace_token] = value

    from src.campaigns.placeholder_semantic import apply_semantic_aliases

    apply_semantic_aliases(string_context, template_text)

    _normalize_territory_context_values(string_context)

    return string_context


def _normalize_territory_context_values(string_context: dict[str, str]) -> None:
    from src.generator.generation.transforms import _normalize_mo_name_case, normalize_russian_geo_admin_case

    mun_name = str(string_context.get("MUN_NAME") or string_context.get("mun_name") or "").strip()
    if mun_name:
        normalized = _normalize_mo_name_case(mun_name)
        string_context["MUN_NAME"] = normalized
        string_context["mun_name"] = normalized

    for key in (
        "MUN_R_NAME",
        "SUB_RF",
        "MUN_R_NAME_1",
        "SUB_RF_1",
        "ADM_NAME_1",
        "MUN_NAME_1",
        "WORK_SCOPE_FRAGMENT",
        "MUN_R_SCOPE_FRAGMENT",
        "HEAD_MO_FRAGMENT",
    ):
        value = str(string_context.get(key) or "").strip()
        if not value:
            continue
        string_context[key] = normalize_russian_geo_admin_case(value)

    adm_name = str(string_context.get("ADM_NAME") or "").strip()
    if adm_name:
        string_context["ADM_NAME"] = normalize_russian_geo_admin_case(adm_name)
