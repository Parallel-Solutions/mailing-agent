from __future__ import annotations

import json
from typing import Any

from src.campaigns.assistants.client import (
    build_assistant_client,
    llm_unavailable_message,
    resolve_model,
    truncate_text,
)
from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.prompts import system_prompt
from src.campaigns.assistants.tools import execute_tool, tools_for_kind
from src.infra.llm_pricing import usage_from_response
from src.infra.spend_ledger import record_llm_usage


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _message_for_openai(message: dict[str, Any]) -> dict[str, Any]:
    result = {"role": message["role"], "content": message.get("content", "")}
    if message["role"] == "assistant" and message.get("tool_calls"):
        result["tool_calls"] = message["tool_calls"]
    if message["role"] == "tool":
        result["tool_call_id"] = message["tool_call_id"]
        result["name"] = message["name"]
    return result


def _serialize_tool_call(tool_call: Any) -> dict[str, Any]:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.function.name,
            "arguments": tool_call.function.arguments,
        },
    }


def _load_arguments(raw_arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_arguments or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def run_assistant_loop(
    *,
    ctx: AssistantContext,
    user_message: str,
    history: list[dict[str, str]],
    max_iterations: int = 8,
) -> dict[str, Any]:
    client = build_assistant_client()
    if client is None:
        reply = llm_unavailable_message()
        return {
            "reply": reply,
            "actions": [],
            "tools_used": [],
        }

    model = resolve_model(ctx.model)
    ctx.model = model
    tools = tools_for_kind(ctx.editor_kind)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(editor_kind=ctx.editor_kind, snapshot=ctx.snapshot)},
        *[{"role": item["role"], "content": item["content"]} for item in history],
        {"role": "user", "content": user_message},
    ]

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=model,
            messages=[_message_for_openai(item) for item in messages],
            tools=tools or None,
            tool_choice="auto" if tools else None,
        )
        record_llm_usage(
            service="openai",
            model=model,
            operation="template_assistant_chat",
            usage=usage_from_response(response),
            owner_username=ctx.owner_username,
        )
        assistant_message = response.choices[0].message
        tool_calls = assistant_message.tool_calls or []
        assistant_content = _safe_text(assistant_message.content)

        if not tool_calls:
            return {
                "reply": assistant_content or "Готово.",
                "actions": list(ctx.actions),
                "tools_used": list(ctx.tools_used),
            }

        serialized_calls = [_serialize_tool_call(call) for call in tool_calls]
        messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": serialized_calls,
            }
        )
        for call in tool_calls:
            name = call.function.name
            args = _load_arguments(call.function.arguments)
            result = execute_tool(ctx, name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": truncate_text(json.dumps(result, ensure_ascii=False, default=str), limit=8000),
                }
            )

    return {
        "reply": "Достигнут лимит шагов агента. Часть изменений могла уже примениться — проверьте редактор.",
        "actions": list(ctx.actions),
        "tools_used": list(ctx.tools_used),
    }
