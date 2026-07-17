from __future__ import annotations

from typing import Any, Callable


def register_system_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    @mcp.tool()
    def get_health() -> dict[str, Any]:
        """Return public health/readiness of the mailing-agent app (no auth)."""
        return get_client().get("/health", auth=False)

    @mcp.tool()
    def get_status() -> dict[str, Any]:
        """Return authenticated app status ping (`/api/status`)."""
        return get_client().get("/api/status")

    @mcp.tool()
    def get_me() -> dict[str, Any]:
        """Return the current authenticated user principal (`/api/auth/me`)."""
        return get_client().get("/api/auth/me")

    @mcp.tool()
    def get_workers_status() -> Any:
        """List background worker process statuses (`/api/workers/status`)."""
        return get_client().get("/api/workers/status")

    @mcp.tool()
    def get_sender_queue() -> Any:
        """Return sender queue snapshot and send-guard status (`/api/sender/queue`)."""
        return get_client().get("/api/sender/queue")
