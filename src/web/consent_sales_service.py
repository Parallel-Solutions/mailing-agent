from __future__ import annotations

import re
from typing import Any

from src.generator.delivery.consent_store import load_consent_records
from src.generator.delivery.sender_agent import _parse_emails, _resolve_sender_data_xlsx_path, _safe_text
from src.generator.generation.excel_io import load_rows

ADMIN_NAME_KEYS = (
    "ADM_NAME",
    "\u041f\u043e\u043b\u043d\u043e\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0438",
    "\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f",
    "\u041d\u0430\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435 \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0438",
)
MUNICIPALITY_KEYS = (
    "MUN_NAME",
    "MUN_R_NAME",
    "\u041c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u043e\u0435 \u043e\u0431\u0440\u0430\u0437\u043e\u0432\u0430\u043d\u0438\u0435",
    "\u041c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u044b\u0439 \u0440\u0430\u0439\u043e\u043d",
)
PHONE_PRIMARY_KEYS = (
    "TEL_OSN",
    "PHONE_OSN",
    "\u0422\u0435\u043b\u0435\u0444\u043e\u043d",
    "\u0422\u0435\u043b\u0435\u0444\u043e\u043d \u043e\u0441\u043d\u043e\u0432\u043d\u043e\u0439",
    "\u0422\u0435\u043b\u0435\u0444\u043e\u043d (\u043e\u0441\u043d\u043e\u0432\u043d\u043e\u0439)",
)
PHONE_EXTRA_KEYS = (
    "TEL_DOP",
    "PHONE_DOP",
    "\u0422\u0435\u043b\u0435\u0444\u043e\u043d (\u0434\u043e\u043f)",
    "\u0422\u0435\u043b\u0435\u0444\u043e\u043d \u0434\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0439",
    "\u0422\u0435\u043b\u0435\u0444\u043e\u043d (\u0434\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0439)",
)
EMAIL_PRIMARY_KEYS = (
    "EMAIL_OSN",
    "EMAIL",
    "E-mail",
    "\u042d\u043b. \u0410\u0434\u0440\u0435\u0441 (\u043e\u0441\u043d\u043e\u0432\u043d\u043e\u0439)",
    "\u042d\u043b. \u0430\u0434\u0440\u0435\u0441",
    "\u041f\u043e\u0447\u0442\u0430",
)
EMAIL_EXTRA_KEYS = (
    "EMAIL_DOP",
    "\u042d\u043b. \u0410\u0434\u0440\u0435\u0441 (\u0434\u043e\u043f)",
    "\u0420\u0435\u0437\u0435\u0440\u0432\u043d\u0430\u044f \u043f\u043e\u0447\u0442\u0430",
    "\u0414\u043e\u043f. \u043f\u043e\u0447\u0442\u0430",
)


def _first_value(row: dict[str, Any], keys: tuple[str, ...], fallback: str = "") -> str:
    for key in keys:
        value = _safe_text(row.get(key))
        if value:
            return value
    return _safe_text(fallback)


def _split_contact_values(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _safe_text(value)
        if not text:
            continue
        for part in re.split(r"[;\n]+", text):
            cleaned = re.sub(r"\s+", " ", part).strip(" ,")
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


def _load_source_rows(job_id: str | None) -> dict[str, dict[str, Any]]:
    try:
        data_path = _resolve_sender_data_xlsx_path(job_id)
        if not data_path.exists():
            return {}
        _, _, rows = load_rows(data_path)
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = _safe_text(row.get("ID"))
        if row_id:
            result[row_id] = row
    return result


def _materials_status_label(record: dict[str, Any]) -> str:
    status = _safe_text(record.get("materials_status")).lower()
    if status == "sent" or _safe_text(record.get("materials_sent_at")):
        return "\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u044b"
    if status == "queued":
        return "\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438"
    if status == "error" or _safe_text(record.get("materials_error")):
        return "\u041e\u0448\u0438\u0431\u043a\u0430 \u043e\u0442\u043f\u0440\u0430\u0432\u043a\u0438 \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u043e\u0432"
    return "\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b \u0435\u0449\u0451 \u043d\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u043b\u0438\u0441\u044c"


def _build_copy_text(item: dict[str, Any]) -> str:
    lines = ["\u041a\u043b\u0438\u0435\u043d\u0442 \u0437\u0430\u043f\u0440\u043e\u0441\u0438\u043b \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b"]
    for label, key in (
        ("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f", "admin_name"),
        ("\u041c\u041e", "municipality"),
        ("Email", "recipient"),
        ("\u0422\u0435\u043b\u0435\u0444\u043e\u043d", "phones_text"),
        ("\u0421\u0442\u0440\u043e\u043a\u0430", "row_id"),
        ("\u041a\u0430\u043c\u043f\u0430\u043d\u0438\u044f", "campaign_name"),
        ("\u0421\u0442\u0430\u0442\u0443\u0441 \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u043e\u0432", "materials_status_label"),
    ):
        value = _safe_text(item.get(key))
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def build_sales_consent_requests(job_id: str | None, *, include_all: bool = False) -> dict[str, Any]:
    source_rows = _load_source_rows(job_id)
    records = load_consent_records(job_id)
    items: list[dict[str, Any]] = []

    for record in records:
        status = _safe_text(record.get("status")) or "pending"
        if not include_all and status != "confirmed":
            continue

        row_id = _safe_text(record.get("row_id"))
        source_row = source_rows.get(row_id, {})
        municipality = _first_value(source_row, MUNICIPALITY_KEYS, fallback=record.get("mun_name"))
        admin_name = _first_value(source_row, ADMIN_NAME_KEYS, fallback=record.get("adm_name") or municipality or record.get("mun_name"))
        primary_emails = _parse_emails(_first_value(source_row, EMAIL_PRIMARY_KEYS, fallback=record.get("email_osn")))
        extra_emails = _parse_emails(_first_value(source_row, EMAIL_EXTRA_KEYS, fallback=record.get("email_dop")))
        phones = _split_contact_values(
            *(_safe_text(source_row.get(key)) for key in PHONE_PRIMARY_KEYS),
            *(_safe_text(source_row.get(key)) for key in PHONE_EXTRA_KEYS),
            record.get("tel_osn"),
            record.get("tel_dop"),
        )

        item = {
            "job_id": _safe_text(record.get("job_id")) or _safe_text(job_id),
            "row_id": row_id,
            "admin_name": admin_name,
            "municipality": municipality,
            "recipient": _safe_text(record.get("recipient")),
            "primary_emails": primary_emails,
            "extra_emails": extra_emails,
            "phones": phones,
            "phones_text": "; ".join(phones),
            "status": status,
            "confirmed_at": _safe_text(record.get("confirmed_at")),
            "request_sent_at": _safe_text(record.get("request_sent_at")),
            "campaign_name": _safe_text(record.get("campaign_name")),
            "work_type": _safe_text(record.get("work_type")),
            "attachment_mode": _safe_text(record.get("attachment_mode")),
            "materials_status": _safe_text(record.get("materials_status")),
            "materials_status_label": _materials_status_label(record),
            "materials_error": _safe_text(record.get("materials_error")),
            "consent_document_path": _safe_text(record.get("consent_document_path")),
        }
        item["copy_text"] = _build_copy_text(item)
        items.append(item)

    items.sort(
        key=lambda item: (item.get("confirmed_at") or item.get("request_sent_at") or "", item.get("row_id") or ""),
        reverse=True,
    )
    return {
        "job_id": _safe_text(job_id),
        "total": len(items),
        "confirmed": sum(1 for item in items if item.get("status") == "confirmed"),
        "items": items,
    }
