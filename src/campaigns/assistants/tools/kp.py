from __future__ import annotations

from typing import Any, Callable

from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.tools import common

ToolHandler = Callable[[AssistantContext, dict[str, Any]], dict[str, Any]]

TOOLS: list[dict[str, Any]] = [
    common.tool_def(
        "get_kp_snapshot",
        "Прочитать текущий HTML КП и переменные.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "list_merge_variables",
        "Список переменных документа.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    common.tool_def(
        "set_body_html",
        "Заменить HTML коммерческого предложения целиком.",
        {
            "type": "object",
            "properties": {"html": {"type": "string"}},
            "required": ["html"],
            "additionalProperties": False,
        },
    ),
    common.tool_def(
        "insert_html",
        "Вставить HTML-фрагмент в макет КП.",
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
    "get_kp_snapshot": common.get_snapshot,
    "list_merge_variables": common.list_merge_variables,
    "set_body_html": common.set_body_html,
    "insert_html": common.insert_html,
    "set_personalization": common.set_personalization,
}
