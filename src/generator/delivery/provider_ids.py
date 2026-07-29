"""Normalize provider message / task IDs for webhook join and status sync.

CampaignFlow historically stored ``provider_message_id`` as ``{transport}:{id}``
(e.g. ``rusender:uuid``). Provider webhooks and API responses use the bare id.
Matching must accept both forms so delivery KPIs leave ``pending``.
"""

from __future__ import annotations

from typing import Any

# Longest prefixes first so ``unisender_go:`` wins over ``unisender:``.
_TRANSPORT_PREFIXES: tuple[str, ...] = (
    "unisender_go:",
    "unisender_classic:",
    "unisender:",
    "mailopost:",
    "rusender:",
    "smtp:",
)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_provider_message_id(value: Any) -> str:
    """Return bare provider message/task id (strip known ``transport:`` prefixes)."""

    text = _safe_text(value)
    if not text:
        return ""
    lowered = text.lower()
    for prefix in _TRANSPORT_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def provider_message_id_lookup_keys(value: Any) -> list[str]:
    """Return unique lookup keys: bare id first, then original if different."""

    original = _safe_text(value)
    bare = normalize_provider_message_id(original)
    keys: list[str] = []
    for key in (bare, original):
        if key and key not in keys:
            keys.append(key)
    return keys
