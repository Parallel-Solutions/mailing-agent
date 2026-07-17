from __future__ import annotations

from src.mcp_server.client import get_client
from src.mcp_server.tools import register_all_tools

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except ImportError:  # pragma: no cover
    FastMCP = None


def create_mcp_server():
    if FastMCP is None:
        raise RuntimeError(
            "MCP package is not installed. Install with: pip install '.[mcp]' "
            "then run: python -m src.mcp_server"
        )

    mcp = FastMCP("mailing-agent-campaign")
    register_all_tools(mcp, get_client)
    return mcp


def main() -> None:
    create_mcp_server().run()


if __name__ == "__main__":
    main()
