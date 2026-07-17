"""GPT-assisted mapping of template variables to recipient list columns."""

from __future__ import annotations

from typing import Any

from src.campaigns.template_ai import _call_llm, list_models

_SYSTEM_PROMPT = (
    "Ты помощник по сопоставлению переменных шаблонов писем и документов "
    "с колонками списка получателей (Excel MO, CSV). "
    "Верни только JSON: "
    '{"mappings":[{"template_variable":"...","recipient_column":"...","confidence":0.0}],'
    '"unmapped":["..."]} '
    "recipient_column должна быть одной из переданных колонок (точное имя). "
    "Если уверенного соответствия нет — добавь переменную в unmapped, не выдумывай колонки."
)


def suggest_mappings_with_ai(
    *,
    template_variables: list[dict[str, Any]],
    recipient_columns: list[str],
    column_samples: dict[str, list[str]],
    already_mapped: dict[str, str],
    model: str = "",
) -> dict[str, Any]:
    pending = [
        item
        for item in template_variables
        if str(item.get("name") or "") not in already_mapped
    ]
    if not pending:
        return {"mappings": [], "unmapped": []}

    var_lines = [
        f"- {item.get('name')}: {item.get('label') or item.get('name')}"
        for item in pending
    ]
    col_lines = []
    for column in recipient_columns:
        samples = column_samples.get(column) or []
        sample_text = ", ".join(samples[:2]) if samples else "(нет примеров)"
        col_lines.append(f"- {column}: {sample_text}")

    user = (
        "Переменные шаблона без сопоставления:\n"
        + "\n".join(var_lines)
        + "\n\nДоступные колонки получателей:\n"
        + "\n".join(col_lines)
    )
    try:
        payload = _call_llm(model, _SYSTEM_PROMPT, user)
    except RuntimeError:
        return {
            "mappings": [],
            "unmapped": [str(item.get("name") or "") for item in pending],
        }

    mappings: list[dict[str, Any]] = []
    allowed_columns = set(recipient_columns)
    for item in payload.get("mappings") or []:
        if not isinstance(item, dict):
            continue
        template_variable = str(item.get("template_variable") or "").strip()
        recipient_column = str(item.get("recipient_column") or "").strip().lower()
        if not template_variable or recipient_column not in allowed_columns:
            continue
        mappings.append(
            {
                "template_variable": template_variable,
                "recipient_column": recipient_column,
                "confidence": float(item.get("confidence") or 0.7),
            }
        )
    unmapped = [
        str(name).strip()
        for name in (payload.get("unmapped") or [])
        if str(name).strip()
    ]
    mapped_names = {item["template_variable"] for item in mappings}
    for item in pending:
        name = str(item.get("name") or "")
        if name and name not in mapped_names and name not in unmapped:
            unmapped.append(name)
    return {"mappings": mappings, "unmapped": unmapped}


def default_model() -> str:
    models = list_models()
    return str(models[0]["id"]) if models else "gpt-4o-mini"
