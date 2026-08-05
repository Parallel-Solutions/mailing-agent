from __future__ import annotations

from typing import Any, Callable


def register_system_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    @mcp.tool()
    def get_health() -> dict[str, Any]:
        """Return public liveness of the mailing-agent app (DB only, no auth)."""
        return get_client().get("/health", auth=False)

    @mcp.tool()
    def get_ready() -> dict[str, Any]:
        """Return public readiness (DB, Redis, MinIO, Gotenberg; no auth)."""
        return get_client().get("/ready", auth=False)

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

    # get_sender_queue removed: it targeted the legacy xlsx sender queue
    # (`/api/sender/queue`, task_type="sender"), which is disabled together
    # with /api/sender/run. CampaignFlow uses task_type="sender_batch"/
    # "chain_followup" instead; there is no replacement endpoint yet.
