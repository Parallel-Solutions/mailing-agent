from __future__ import annotations

from typing import Any, Callable


def register_profile_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    @mcp.tool()
    def get_profile() -> Any:
        """Get or create the CampaignFlow user profile."""
        return get_client().get("/api/v1/profile")

    @mcp.tool()
    def update_profile(fields: dict[str, Any]) -> Any:
        """Patch profile fields (display name, email, company, signature, timezone, mailing_defaults, …)."""
        return get_client().patch("/api/v1/profile", fields)

    @mcp.tool()
    def list_work_types() -> Any:
        """List available mailing work types."""
        return get_client().get("/api/v1/work-types")

    @mcp.tool()
    def create_work_type(body: dict[str, Any]) -> Any:
        """Create a custom work type. Body matches POST /api/v1/work-types."""
        return get_client().post("/api/v1/work-types", body)
