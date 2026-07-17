from __future__ import annotations

from typing import Any

from src.security.mcp_tokens import extract_bearer_token, parse_mcp_tokens, resolve_mcp_token_username
from src.security.session_store import get_session_username


def resolve_request_username(
    *,
    session_token: str | None,
    authorization: str | None,
    settings_obj: Any,
) -> str | None:
    """Resolve username from Bearer (MCP token or session) or session cookie."""
    ttl_days = max(1, int(getattr(settings_obj, "app_session_ttl_days", 7) or 7))
    bearer = extract_bearer_token(authorization)
    if bearer:
        mcp_username = resolve_mcp_token_username(
            bearer,
            parse_mcp_tokens(getattr(settings_obj, "mailing_agent_mcp_tokens", "")),
        )
        if mcp_username:
            return mcp_username
        session_user = get_session_username(bearer, ttl_days=ttl_days)
        if session_user:
            return session_user
    return get_session_username(session_token, ttl_days=ttl_days)
