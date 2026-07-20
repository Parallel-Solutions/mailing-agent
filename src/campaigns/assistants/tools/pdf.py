from __future__ import annotations

from typing import Any, Callable

from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.tools import common

ToolHandler = Callable[[AssistantContext, dict[str, Any]], dict[str, Any]]


def list_fields(ctx: AssistantContext, _args: dict[str, Any]) -> dict[str, Any]:
    fields = ctx.working.get("fields") or ctx.snapshot.get("fields") or []
    return {"ok": True, "fields": fields}


def update_fields(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    raw_fields = args.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        return {"ok": False, "error": "fields должен быть непустым списком"}
    updates: list[dict[str, Any]] = []
    for item in raw_fields:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        patch: dict[str, Any] = {"id": str(item["id"])}
        if "value" in item:
            patch["value"] = str(item.get("value") or "")[:500]
        if "font_size" in item and item["font_size"] is not None:
            try:
                size = float(item["font_size"])
            except (TypeError, ValueError):
                continue
            patch["font_size"] = max(6.0, min(36.0, size))
        updates.append(patch)
    if not updates:
        return {"ok": False, "error": "нет корректных обновлений полей"}

    current = list(ctx.working.get("fields") or ctx.snapshot.get("fields") or [])
    by_id = {str(field.get("id")): dict(field) for field in current if isinstance(field, dict)}
    for patch in updates:
        field = by_id.get(patch["id"])
        if not field:
            continue
        if "value" in patch:
            field["value"] = patch["value"]
        if "font_size" in patch:
            field["font_size"] = patch["font_size"]
        by_id[patch["id"]] = field
    ctx.working["fields"] = list(by_id.values())
    return ctx.emit("update_pdf_fields", fields=updates)


TOOLS: list[dict[str, Any]] = [
    common.tool_def(
        "get_pdf_snapshot",
        "Прочитать snapshot PDF-редактора (страницы и поля).",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "list_fields",
        "Список редактируемых жёлтых полей PDF.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "update_fields",
        "Обновить значения/размер шрифта полей PDF.",
        {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "value": {"type": "string"},
                            "font_size": {"type": "number"},
                        },
                        "required": ["id"],
                    },
                }
            },
            "required": ["fields"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "set_personalization",
        "Включить/выключить персонализацию.",
        {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
            "additionalProperties": False,
        },
    ),
]

HANDLERS: dict[str, ToolHandler] = {
    "get_pdf_snapshot": common.get_snapshot,
    "list_fields": list_fields,
    "update_fields": update_fields,
    "set_personalization": common.set_personalization,
}
