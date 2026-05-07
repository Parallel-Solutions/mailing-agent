from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4


SESSION_TTL = timedelta(hours=2)
SESSIONS: dict[str, dict[str, Any]] = {}


@dataclass
class UserGoalState:
    goal: str | None = None
    status: str = "unknown"
    steps_completed: list[str] = field(default_factory=list)
    steps_pending: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_session_id() -> str:
    return str(uuid4())


def cleanup_old_sessions() -> None:
    now = datetime.now()
    stale_ids = [
        session_id
        for session_id, payload in SESSIONS.items()
        if now - payload.get("updated_at", now) > SESSION_TTL
    ]
    for session_id in stale_ids:
        SESSIONS.pop(session_id, None)


def get_session(session_id: str | None) -> tuple[str, dict[str, Any]]:
    cleanup_old_sessions()
    resolved_session_id = session_id or generate_session_id()
    if resolved_session_id not in SESSIONS:
        SESSIONS[resolved_session_id] = {
            "history": [],
            "goal": UserGoalState(),
            "updated_at": datetime.now(),
        }
    else:
        SESSIONS[resolved_session_id]["updated_at"] = datetime.now()
    return resolved_session_id, SESSIONS[resolved_session_id]


def append_message(session: dict[str, Any], role: str, content: Any, **extra: Any) -> None:
    message = {"role": role, "content": content}
    message.update(extra)
    session.setdefault("history", []).append(message)
    session["updated_at"] = datetime.now()


def get_goal_state(session: dict[str, Any]) -> UserGoalState:
    goal_state = session.get("goal")
    if isinstance(goal_state, UserGoalState):
        return goal_state
    if isinstance(goal_state, dict):
        restored = UserGoalState(**goal_state)
        session["goal"] = restored
        return restored
    restored = UserGoalState()
    session["goal"] = restored
    return restored

