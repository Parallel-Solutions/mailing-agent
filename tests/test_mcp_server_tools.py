from __future__ import annotations

import unittest

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except ImportError:  # pragma: no cover
    FastMCP = None


@unittest.skipIf(FastMCP is None, "mcp package not installed")
class McpServerToolsTests(unittest.TestCase):
    def test_create_mcp_server_registers_tools(self) -> None:
        from src.mcp_server.server import create_mcp_server

        server = create_mcp_server()
        tools = server._tool_manager.list_tools()  # noqa: SLF001 - FastMCP internal
        names = sorted(tool.name for tool in tools)
        self.assertTrue(names)
        for expected in (
            "get_health",
            "get_me",
            "list_campaigns",
            "launch_campaign",
            "list_connections",
            "list_templates",
            "list_audiences",
            "get_manager_dashboard",
        ):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()
