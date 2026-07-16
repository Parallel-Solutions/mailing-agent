from __future__ import annotations

from typing import Any, Callable


def register_template_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    @mcp.tool()
    def list_templates(q: str | None = None, limit: int | None = None, offset: int | None = None) -> Any:
        """List mail templates."""
        return get_client().get(
            "/api/v1/templates",
            params={"q": q, "limit": limit, "offset": offset},
        )

    @mcp.tool()
    def get_template(template_id: str) -> Any:
        """Get a template by id."""
        return get_client().get(f"/api/v1/templates/{template_id}")

    @mcp.tool()
    def create_template(body: dict[str, Any]) -> Any:
        """Create a template. Body matches POST /api/v1/templates."""
        return get_client().post("/api/v1/templates", body)

    @mcp.tool()
    def update_template(template_id: str, body: dict[str, Any]) -> Any:
        """Save a new template version / patch fields."""
        return get_client().patch(f"/api/v1/templates/{template_id}", body)

    @mcp.tool()
    def upload_template(
        file_path: str,
        template_type: str,
        name: str | None = None,
        template_id: str | None = None,
    ) -> Any:
        """Upload a document template file from a local path. template_type is required (document; kp/contract aliases accepted)."""
        extra: dict[str, str] = {"template_type": template_type}
        if name:
            extra["name"] = name
        if template_id:
            extra["template_id"] = template_id
        return get_client().upload_file(
            "/api/v1/templates/upload",
            file_path,
            extra_fields=extra,
        )

    @mcp.tool()
    def duplicate_template(template_id: str) -> Any:
        """Duplicate a template."""
        return get_client().post(f"/api/v1/templates/{template_id}/duplicate")

    @mcp.tool()
    def archive_template(template_id: str) -> Any:
        """Archive a template."""
        return get_client().post(f"/api/v1/templates/{template_id}/archive")

    @mcp.tool()
    def list_template_versions(template_id: str) -> Any:
        """List version history for a template."""
        return get_client().get(f"/api/v1/templates/{template_id}/versions")

    @mcp.tool()
    def preview_template(template_id: str, body: dict[str, Any] | None = None) -> Any:
        """Render a template preview (subject/body_html)."""
        return get_client().post(f"/api/v1/templates/{template_id}/preview", body or {})
