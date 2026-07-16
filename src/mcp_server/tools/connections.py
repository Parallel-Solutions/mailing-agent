from __future__ import annotations

from typing import Any, Callable


def register_connection_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    @mcp.tool()
    def list_connections() -> Any:
        """List delivery connections (SMTP / API providers)."""
        return get_client().get("/api/v1/connections")

    @mcp.tool()
    def create_connection(body: dict[str, Any]) -> Any:
        """Create a delivery connection. Body matches POST /api/v1/connections."""
        return get_client().post("/api/v1/connections", body)

    @mcp.tool()
    def update_connection(connection_id: str, body: dict[str, Any]) -> Any:
        """Patch a delivery connection by id."""
        return get_client().patch(f"/api/v1/connections/{connection_id}", body)

    @mcp.tool()
    def delete_connection(connection_id: str) -> Any:
        """Delete a delivery connection by id."""
        return get_client().delete(f"/api/v1/connections/{connection_id}")

    @mcp.tool()
    def test_connection(connection_id: str) -> Any:
        """Send a connection test for the given connection id."""
        return get_client().post(f"/api/v1/connections/{connection_id}/test")

    @mcp.tool()
    def analyze_smtp_setup(email: str) -> Any:
        """Analyze SMTP setup for an email (discover provider settings / next actions)."""
        return get_client().post("/api/smtp/setup/analyze", {"email": email})

    @mcp.tool()
    def verify_smtp_setup(body: dict[str, Any]) -> Any:
        """Verify SMTP credentials for a setup session. Body matches POST /api/smtp/setup/verify."""
        return get_client().post("/api/smtp/setup/verify", body)

    @mcp.tool()
    def get_smtp_oauth_start_url(provider: str, email: str, setup_session_id: str) -> Any:
        """Return OAuth authorize_url for SMTP setup (browser OAuth still required in UI)."""
        return get_client().get(
            "/api/smtp/oauth/start",
            params={
                "provider": provider,
                "email": email,
                "setup_session_id": setup_session_id,
            },
        )
