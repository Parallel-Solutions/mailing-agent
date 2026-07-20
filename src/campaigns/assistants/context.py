from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EDITOR_KINDS = frozenset(
    {
        "visual_email",
        "simple_email",
        "kp",
        "pdf",
        "docx",
        "chain",
    }
)


@dataclass
class AssistantContext:
    editor_kind: str
    resource_id: str
    owner_username: str
    is_admin: bool = False
    model: str = ""
    snapshot: dict[str, Any] = field(default_factory=dict)
    working: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)

    def emit(self, action_type: str, **payload: Any) -> dict[str, Any]:
        action = {"type": action_type, **payload}
        self.actions.append(action)
        return {"ok": True, "action": action}
