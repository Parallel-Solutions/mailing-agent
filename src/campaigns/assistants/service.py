from __future__ import annotations

from typing import Any

from src.campaigns.assistants.context import EDITOR_KINDS, AssistantContext
from src.campaigns.assistants.loop import run_assistant_loop
from src.campaigns.assistants.session import history_for_prompt, open_assistant_session, persist_turn
from src.campaigns.assistants.tools.common import clip_snapshot


def run_editor_assistant(
    *,
    editor_kind: str,
    resource_id: str,
    message: str,
    owner_username: str,
    is_admin: bool = False,
    session_id: str | None = None,
    model: str | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = (editor_kind or "").strip()
    if kind not in EDITOR_KINDS:
        raise ValueError(
            "editor_kind должен быть одним из: "
            + ", ".join(sorted(EDITOR_KINDS))
        )
    text = (message or "").strip()
    if not text:
        raise ValueError("Сообщение не может быть пустым")
    rid = (resource_id or "").strip()
    if not rid:
        raise ValueError("resource_id обязателен")

    clipped = clip_snapshot(snapshot)
    working = dict(snapshot or {})
    if kind == "chain":
        working.setdefault("selected_node_id", (snapshot or {}).get("selected_node_id"))

    session_key, session = open_assistant_session(session_id, editor_kind=kind)
    history = history_for_prompt(session)

    ctx = AssistantContext(
        editor_kind=kind,
        resource_id=rid,
        owner_username=owner_username,
        is_admin=is_admin,
        model=model or "",
        snapshot=clipped,
        working=working,
    )
    result = run_assistant_loop(ctx=ctx, user_message=text, history=history)
    reply = str(result.get("reply") or "Готово.")
    persist_turn(session_key, text, reply)
    return {
        "reply": reply,
        "session_id": session_key,
        "tools_used": result.get("tools_used") or [],
        "actions": result.get("actions") or [],
        "editor_kind": kind,
        "resource_id": rid,
    }
