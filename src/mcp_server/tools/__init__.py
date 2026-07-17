from __future__ import annotations

from typing import Any, Callable


def register_all_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    from src.mcp_server.tools.audiences import register_audience_tools
    from src.mcp_server.tools.campaigns import register_campaign_tools
    from src.mcp_server.tools.connections import register_connection_tools
    from src.mcp_server.tools.profile import register_profile_tools
    from src.mcp_server.tools.statistics import register_statistics_tools
    from src.mcp_server.tools.system import register_system_tools
    from src.mcp_server.tools.templates import register_template_tools

    register_system_tools(mcp, get_client)
    register_profile_tools(mcp, get_client)
    register_connection_tools(mcp, get_client)
    register_campaign_tools(mcp, get_client)
    register_template_tools(mcp, get_client)
    register_audience_tools(mcp, get_client)
    register_statistics_tools(mcp, get_client)
