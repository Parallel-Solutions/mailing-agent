from __future__ import annotations

import json
from typing import Any

from src.generator.inflection.ai_case_agent import OpenAI, _build_openai_http_client, _resolve_openai_api_key, _resolve_openai_base_url
from src.generator.orchestration.orchestrator_prompts import get_orchestrator_system_prompt
from src.generator.orchestration.orchestrator_session_state import append_message, get_goal_state, get_session
from src.generator.orchestration.orchestrator_tool_executor import execute_tool
from src.generator.orchestration.orchestrator_tools import ORCHESTRATOR_TOOLS
from src.utils.config import settings


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _build_llm_client() -> OpenAI | None:
    if not OpenAI:
        return None
    api_key = _resolve_openai_api_key()
    if not api_key:
        return None
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        kwargs["base_url"] = base_url
    http_client = _build_openai_http_client()
    if http_client:
        kwargs["http_client"] = http_client
    try:
        return OpenAI(**kwargs)
    except Exception:
        return None


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


def _classify_confirmation_response(client: OpenAI | None, user_message: str, pending_confirmation: dict[str, Any]) -> str:
    if not client:
        return "unclear"
    recipients = pending_confirmation.get("rows") or []
    prompt = (
        "Определи, является ли сообщение пользователя подтверждением или отказом "
        "от реальной email-отправки по ранее показанным адресам.\n"
        "Верни только одно слово: confirm, cancel или unclear.\n\n"
        f"Показанные адреса:\n{json.dumps(recipients, ensure_ascii=False)}\n\n"
        f"Сообщение пользователя:\n{user_message}"
    )
    try:
        response = client.chat.completions.create(
            model=settings.case_agent_model,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = _safe_text(response.choices[0].message.content).lower()
    except Exception:
        return "unclear"
    if answer.startswith("confirm"):
        return "confirm"
    if answer.startswith("cancel"):
        return "cancel"
    return "unclear"


def run_agentic_orchestrator(
    *,
    user_message: str,
    session_id: str | None,
    snapshot_builder,
    preflight_builder,
    analysis_builder,
    state_setter,
    max_iterations: int = 5,
) -> dict[str, Any]:
    resolved_session_id, session = get_session(session_id)
    goal_state = get_goal_state(session)
    pending_confirmation = goal_state.context.get("pending_send_confirmation") or {}
    goal_state.goal = user_message
    goal_state.status = "planning"
    append_message(session, "user", user_message)

    client = _build_llm_client()

    if pending_confirmation.get("awaiting") and not pending_confirmation.get("approved"):
        confirmation_decision = _classify_confirmation_response(client, user_message, pending_confirmation)
        if confirmation_decision == "cancel":
            goal_state.context["pending_send_confirmation"] = {
                "awaiting": False,
                "approved": False,
                "rows": [],
                "checked_at": pending_confirmation.get("checked_at"),
            }
            reply = "Хорошо, реальную отправку не запускаю. Если нужно, можем проверить строки ещё раз или скорректировать почты."
            append_message(session, "assistant", reply)
            snapshot = snapshot_builder()
            preflight = preflight_builder()
            analysis, risks, options = analysis_builder(preflight, snapshot)
            state = state_setter(
                goal=user_message,
                action="send_cancelled",
                plan=[],
                result=reply,
                analysis=analysis,
                risks=risks,
                options=options,
                session_id=resolved_session_id,
            )
            return {"reply": reply, "state": state, "session_id": resolved_session_id, "downloads": []}

        if confirmation_decision == "confirm":
            goal_state.context["pending_send_confirmation"] = {
                **pending_confirmation,
                "awaiting": False,
                "approved": True,
            }
            append_message(
                session,
                "user",
                "Пользователь подтвердил, что найденные почты верные и реальную отправку можно запускать.",
            )

    downloads: list[dict[str, Any]] = []
    if not client:
        reply = "Сейчас не удалось подключиться к LLM-слою оркестратора. Временно вернись позже или используй legacy-режим."
        append_message(session, "assistant", reply)
        state = state_setter(
            goal=user_message,
            action="agentic_unavailable",
            plan=[],
            result=reply,
            analysis="",
            risks=[],
            options=[],
            session_id=resolved_session_id,
        )
        return {"reply": reply, "state": state, "session_id": resolved_session_id, "downloads": downloads}

    last_tool_name = ""
    for _ in range(max_iterations):
        snapshot = snapshot_builder()
        preflight = preflight_builder()
        analysis, risks, options = analysis_builder(preflight, snapshot)
        system_prompt = get_orchestrator_system_prompt(
            snapshot=snapshot,
            preflight=preflight,
            goal_state=goal_state,
        )
        messages = [_message_for_openai(item) for item in session.get("history", [])]
        response = client.chat.completions.create(
            model=settings.case_agent_model,
            messages=[{"role": "system", "content": system_prompt}, *messages],
            tools=ORCHESTRATOR_TOOLS,
            tool_choice="auto",
        )
        assistant_message = response.choices[0].message
        tool_calls = assistant_message.tool_calls or []
        assistant_content = _safe_text(assistant_message.content)

        if not tool_calls:
            final_reply = assistant_content or "Готово."
            goal_state.status = "completed"
            append_message(session, "assistant", final_reply)
            state = state_setter(
                goal=user_message,
                action=last_tool_name or "agentic_reply",
                plan=goal_state.steps_pending,
                result=final_reply,
                analysis=analysis,
                risks=risks,
                options=options,
                session_id=resolved_session_id,
            )
            return {
                "reply": final_reply,
                "state": state,
                "session_id": resolved_session_id,
                "downloads": downloads,
            }

        serialized_tool_calls = [_serialize_tool_call(call) for call in tool_calls]
        append_message(
            session,
            "assistant",
            assistant_content or "",
            tool_calls=serialized_tool_calls,
        )

        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            last_tool_name = tool_name
            tool_args = _load_arguments(tool_call.function.arguments)
            tool_result = execute_tool(
                tool_name,
                tool_args,
                preflight=preflight,
                snapshot=snapshot,
                analysis=analysis,
                risks=risks,
                options=options,
                goal_state=goal_state,
            )
            for item in tool_result.get("details", {}).get("downloads", []) or []:
                if item not in downloads:
                    downloads.append(item)
            append_message(
                session,
                "tool",
                json.dumps(tool_result, ensure_ascii=False),
                tool_call_id=tool_call.id,
                name=tool_name,
            )

    goal_state.status = "blocked"
    fallback_reply = (
        "Я сделал несколько шагов, но не смог безопасно завершить задачу за один проход. "
        f"Последний этап: {last_tool_name or 'без tool call'}. "
        "Попробуй уточнить запрос или разбить задачу на более короткий шаг."
    )
    append_message(session, "assistant", fallback_reply)
    snapshot = snapshot_builder()
    preflight = preflight_builder()
    analysis, risks, options = analysis_builder(preflight, snapshot)
    state = state_setter(
        goal=user_message,
        action="agentic_max_iterations",
        plan=goal_state.steps_pending,
        result=fallback_reply,
        analysis=analysis,
        risks=risks,
        options=options,
        session_id=resolved_session_id,
    )
    return {
        "reply": fallback_reply,
        "state": state,
        "session_id": resolved_session_id,
        "downloads": downloads,
    }
