from __future__ import annotations

import json
import secrets
from typing import Any


def parse_mcp_tokens(raw: Any) -> dict[str, str]:
    """Parse MAILING_AGENT_MCP_TOKENS JSON map: token -> username."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for raw_token, raw_username in parsed.items():
        token = str(raw_token or "").strip()
        username = str(raw_username or "").strip()
        if token and username:
            out[token] = username
    return out


def resolve_mcp_token_username(token: str, tokens_map: dict[str, str]) -> str | None:
    candidate = str(token or "").strip()
    if not candidate or not tokens_map:
        return None
    for known_token, username in tokens_map.items():
        if secrets.compare_digest(candidate, known_token):
            return username
    return None


def extract_bearer_token(authorization: str | None) -> str | None:
    raw = str(authorization or "").strip()
    if not raw:
        return None
    scheme, _, remainder = raw.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = remainder.strip()
    return token or None
