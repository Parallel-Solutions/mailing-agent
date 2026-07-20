from __future__ import annotations

from typing import Any, Callable

from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.tools import common

ToolHandler = Callable[[AssistantContext, dict[str, Any]], dict[str, Any]]


def set_grapes_project(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    project = args.get("project") or args.get("grapesjs_project")
    if not isinstance(project, dict):
        return {"ok": False, "error": "project должен быть объектом GrapesJS"}
    ctx.working["grapesjs_project"] = project
    return ctx.emit("load_grapes_project", project=project)


def insert_block_html(ctx: AssistantContext, args: dict[str, Any]) -> dict[str, Any]:
    html = str(args.get("html") or "")
    if not html.strip():
        return {"ok": False, "error": "html пуст"}
    return ctx.emit("insert_components", html=html)


TOOLS: list[dict[str, Any]] = [
    common.tool_def(
        "get_email_snapshot",
        "Прочитать текущий snapshot письма (тема, HTML, переменные).",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "list_merge_variables",
        "Список доступных переменных слияния {{var}}.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "set_subject",
        "Установить тему письма.",
        {
            "type": "object",
            "properties": {"subject": {"type": "string"}},
            "required": ["subject"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "set_body_html",
        "Заменить HTML тела письма целиком.",
        {
            "type": "object",
            "properties": {"html": {"type": "string"}},
            "required": ["html"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "set_grapes_project",
        "Загрузить полный GrapesJS projectData.",
        {
            "type": "object",
            "properties": {"project": {"type": "object"}},
            "required": ["project"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "insert_block_html",
        "Вставить HTML-блок на холст (как addComponents).",
        {
            "type": "object",
            "properties": {"html": {"type": "string"}},
            "required": ["html"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "set_personalization",
        "Включить/выключить персонализацию для каждого получателя.",
        {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
            "additionalProperties": False,
        },
    ),
]

HANDLERS: dict[str, ToolHandler] = {
    "get_email_snapshot": common.get_snapshot,
    "list_merge_variables": common.list_merge_variables,
    "set_subject": common.set_subject,
    "set_body_html": common.set_body_html,
    "set_grapes_project": set_grapes_project,
    "insert_block_html": insert_block_html,
    "set_personalization": common.set_personalization,
}
