from __future__ import annotations

from typing import Any

from src.jobs.chat_memory import append_chat_turn, chat_history_for_prompt, get_chat_session


def open_assistant_session(
    session_id: str | None,
    *,
    editor_kind: str,
) -> tuple[str, dict[str, Any]]:
    namespace = f"assistant-{editor_kind}"
    return get_chat_session(session_id, namespace=namespace, job_id=None)


def history_for_prompt(session: dict[str, Any]) -> list[dict[str, str]]:
    return chat_history_for_prompt(session, limit=8)


def persist_turn(session_id: str, user_message: str, assistant_reply: str) -> None:
    append_chat_turn(session_id, user_message, assistant_reply)
