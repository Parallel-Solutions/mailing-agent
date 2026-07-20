from __future__ import annotations

from typing import Any, Callable

from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.tools import chain, docx, kp, pdf, simple_email, visual_email

ToolHandler = Callable[[AssistantContext, dict[str, Any]], dict[str, Any]]

_REGISTRY: dict[str, tuple[list[dict[str, Any]], dict[str, ToolHandler]]] = {
    "visual_email": (visual_email.TOOLS, visual_email.HANDLERS),
    "simple_email": (simple_email.TOOLS, simple_email.HANDLERS),
    "kp": (kp.TOOLS, kp.HANDLERS),
    "pdf": (pdf.TOOLS, pdf.HANDLERS),
    "docx": (docx.TOOLS, docx.HANDLERS),
    "chain": (chain.TOOLS, chain.HANDLERS),
}


def tools_for_kind(editor_kind: str) -> list[dict[str, Any]]:
    pair = _REGISTRY.get(editor_kind)
    if not pair:
        return []
    return pair[0]


def execute_tool(ctx: AssistantContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    pair = _REGISTRY.get(ctx.editor_kind)
    if not pair:
        return {"ok": False, "error": f"Неизвестный editor_kind: {ctx.editor_kind}"}
    handler = pair[1].get(name)
    if handler is None:
        return {"ok": False, "error": f"Инструмент недоступен: {name}"}
    try:
        result = handler(ctx, args or {})
    except Exception as exc:  # pragma: no cover - surfaced to the model
        return {"ok": False, "error": str(exc)}
    ctx.tools_used.append(name)
    return result if isinstance(result, dict) else {"ok": True, "result": result}
