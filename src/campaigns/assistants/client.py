from __future__ import annotations

from typing import Any

from src.campaigns.template_ai import _build_client, list_models


def build_assistant_client():
    return _build_client()


def resolve_model(model: str | None) -> str:
    allowed = {item["id"] for item in list_models()}
    candidate = (model or "").strip()
    if candidate in allowed:
        return candidate
    return next(iter(allowed), "gpt-4o-mini")


def llm_unavailable_message() -> str:
    return (
        "Сейчас не удалось подключиться к модели. "
        "Проверьте OPENAI_API_KEY / OPENAI_BASE_URL и попробуйте снова."
    )


def truncate_text(value: Any, *, limit: int = 12000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
