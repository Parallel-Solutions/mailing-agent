from __future__ import annotations

from typing import Any, Callable


def register_audience_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    @mcp.tool()
    def list_audiences() -> Any:
        """List reusable audiences."""
        return get_client().get("/api/v1/audiences")

    @mcp.tool()
    def get_audience(audience_id: str) -> Any:
        """Get an audience by id."""
        return get_client().get(f"/api/v1/audiences/{audience_id}")

    @mcp.tool()
    def create_audience(body: dict[str, Any]) -> Any:
        """Create an audience. Body matches POST /api/v1/audiences."""
        return get_client().post("/api/v1/audiences", body)

    @mcp.tool()
    def update_audience(audience_id: str, body: dict[str, Any]) -> Any:
        """Rename / patch an audience."""
        return get_client().patch(f"/api/v1/audiences/{audience_id}", body)

    @mcp.tool()
    def duplicate_audience(audience_id: str) -> Any:
        """Duplicate an audience."""
        return get_client().post(f"/api/v1/audiences/{audience_id}/duplicate")

    @mcp.tool()
    def list_audience_members(
        audience_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """List members of an audience."""
        return get_client().get(
            f"/api/v1/audiences/{audience_id}/members",
            params={"limit": limit, "offset": offset},
        )

    @mcp.tool()
    def replace_audience_members(audience_id: str, members: list[dict[str, Any]]) -> Any:
        """Replace all members of an audience. Pass a list of member objects."""
        return get_client().put(
            f"/api/v1/audiences/{audience_id}/members",
            {"members": members},
        )

    @mcp.tool()
    def import_audience_members(audience_id: str, file_path: str) -> Any:
        """Import audience members from a local CSV/XLSX file path on the MCP host."""
        return get_client().upload_file(
            f"/api/v1/audiences/{audience_id}/import",
            file_path,
        )

    @mcp.tool()
    def use_audience_in_campaign(audience_id: str, campaign_id: str) -> Any:
        """Copy an audience into a campaign's recipients."""
        return get_client().post(
            f"/api/v1/audiences/{audience_id}/use-in-campaign/{campaign_id}"
        )
