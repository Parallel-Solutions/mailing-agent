"""CampaignFlow MCP server (HTTP bridge to a running mailing-agent app)."""

from __future__ import annotations

from typing import Any

__all__ = ["create_mcp_server"]


def __getattr__(name: str) -> Any:
    if name == "create_mcp_server":
        from src.mcp_server.server import create_mcp_server

        return create_mcp_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
