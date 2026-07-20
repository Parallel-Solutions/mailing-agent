from __future__ import annotations

from typing import Any, Callable

from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.tools import common

ToolHandler = Callable[[AssistantContext, dict[str, Any]], dict[str, Any]]

TOOLS: list[dict[str, Any]] = [
    common.tool_def(
        "get_email_snapshot",
        "Прочитать текущий snapshot простого письма.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "list_merge_variables",
        "Список переменных слияния.",
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
        "Заменить HTML содержимое редактора.",
        {
            "type": "object",
            "properties": {"html": {"type": "string"}},
            "required": ["html"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "insert_html",
        "Вставить HTML-фрагмент в позицию курсора/в конец.",
        {
            "type": "object",
            "properties": {"html": {"type": "string"}},
            "required": ["html"],
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
    "get_email_snapshot": common.get_snapshot,
    "list_merge_variables": common.list_merge_variables,
    "set_subject": common.set_subject,
    "set_body_html": common.set_body_html,
    "insert_html": common.insert_html,
    "set_personalization": common.set_personalization,
}
