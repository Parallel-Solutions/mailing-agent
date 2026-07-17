from __future__ import annotations

from typing import Any, Callable


def register_campaign_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    @mcp.tool()
    def list_campaigns(
        status: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """List campaigns. Optional filters: status, q, limit, offset."""
        return get_client().get(
            "/api/v1/campaigns",
            params={"status": status, "q": q, "limit": limit, "offset": offset},
        )

    @mcp.tool()
    def get_active_sending() -> Any:
        """Return the dashboard active-sending block."""
        return get_client().get("/api/v1/campaigns/active-sending")

    @mcp.tool()
    def get_campaign(campaign_id: str) -> Any:
        """Get a campaign by id."""
        return get_client().get(f"/api/v1/campaigns/{campaign_id}")

    @mcp.tool()
    def create_campaign(body: dict[str, Any]) -> Any:
        """Create a campaign draft. Body matches POST /api/v1/campaigns."""
        return get_client().post("/api/v1/campaigns", body)

    @mcp.tool()
    def update_campaign(campaign_id: str, body: dict[str, Any]) -> Any:
        """Patch / autosave a campaign draft."""
        return get_client().patch(f"/api/v1/campaigns/{campaign_id}", body)

    @mcp.tool()
    def duplicate_campaign(campaign_id: str) -> Any:
        """Duplicate a campaign."""
        return get_client().post(f"/api/v1/campaigns/{campaign_id}/duplicate")

    @mcp.tool()
    def archive_campaign(campaign_id: str) -> Any:
        """Archive a campaign."""
        return get_client().post(f"/api/v1/campaigns/{campaign_id}/archive")

    @mcp.tool()
    def list_campaign_recipients(
        campaign_id: str,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> Any:
        """List paginated recipients for a campaign."""
        return get_client().get(
            f"/api/v1/campaigns/{campaign_id}/recipients",
            params={"limit": limit, "offset": offset, "q": q},
        )

    @mcp.tool()
    def replace_campaign_recipients(campaign_id: str, recipients: list[dict[str, Any]]) -> Any:
        """Replace all recipients for a campaign. Pass a list of recipient objects."""
        return get_client().put(
            f"/api/v1/campaigns/{campaign_id}/recipients",
            {"recipients": recipients},
        )

    @mcp.tool()
    def update_campaign_recipient(
        campaign_id: str,
        recipient_id: int,
        body: dict[str, Any],
    ) -> Any:
        """Patch one campaign recipient by numeric recipient_id."""
        return get_client().patch(
            f"/api/v1/campaigns/{campaign_id}/recipients/{recipient_id}",
            body,
        )

    @mcp.tool()
    def delete_campaign_recipients(campaign_id: str, ids: list[int]) -> Any:
        """Bulk-delete campaign recipients by numeric id list."""
        return get_client().post(
            f"/api/v1/campaigns/{campaign_id}/recipients/delete",
            {"ids": ids},
        )

    @mcp.tool()
    def import_campaign_recipients(campaign_id: str, file_path: str) -> Any:
        """Import recipients from a local CSV/XLSX file path on the MCP host."""
        return get_client().upload_file(
            f"/api/v1/campaigns/{campaign_id}/recipients/import",
            file_path,
        )

    @mcp.tool()
    def get_campaign_schedule(campaign_id: str) -> Any:
        """Get campaign schedule settings."""
        return get_client().get(f"/api/v1/campaigns/{campaign_id}/schedule")

    @mcp.tool()
    def update_campaign_schedule(campaign_id: str, body: dict[str, Any]) -> Any:
        """Update campaign schedule settings."""
        return get_client().put(f"/api/v1/campaigns/{campaign_id}/schedule", body)

    @mcp.tool()
    def preview_schedule(body: dict[str, Any]) -> Any:
        """Preview schedule batches without saving (POST /api/v1/schedule/preview)."""
        return get_client().post("/api/v1/schedule/preview", body)

    @mcp.tool()
    def validate_campaign(campaign_id: str) -> Any:
        """Run pre-launch validation for a campaign."""
        return get_client().get(f"/api/v1/campaigns/{campaign_id}/validate")

    @mcp.tool()
    def launch_campaign(campaign_id: str, force_now: bool = False) -> Any:
        """Launch a campaign (create batches and enqueue). Set force_now=true to send immediately."""
        return get_client().post(
            f"/api/v1/campaigns/{campaign_id}/launch",
            params={"force_now": force_now},
        )

    @mcp.tool()
    def pause_campaign(campaign_id: str) -> Any:
        """Pause a running campaign."""
        return get_client().post(f"/api/v1/campaigns/{campaign_id}/pause")

    @mcp.tool()
    def resume_campaign(campaign_id: str) -> Any:
        """Resume a paused campaign."""
        return get_client().post(f"/api/v1/campaigns/{campaign_id}/resume")

    @mcp.tool()
    def cancel_campaign(campaign_id: str) -> Any:
        """Cancel a campaign."""
        return get_client().post(f"/api/v1/campaigns/{campaign_id}/cancel")

    @mcp.tool()
    def list_campaign_batches(campaign_id: str) -> Any:
        """List send batches for a campaign."""
        return get_client().get(f"/api/v1/campaigns/{campaign_id}/batches")

    @mcp.tool()
    def cancel_campaign_batch(campaign_id: str, batch_id: str) -> Any:
        """Cancel a future campaign batch."""
        return get_client().post(f"/api/v1/campaigns/{campaign_id}/batches/{batch_id}/cancel")

    @mcp.tool()
    def send_campaign_test_email(
        campaign_id: str,
        to_email: str,
        smtp_mailbox_id: str | None = None,
    ) -> Any:
        """Send a test email for a campaign via Mailpit/SMTP."""
        body: dict[str, Any] = {"to_email": to_email}
        if smtp_mailbox_id:
            body["smtp_mailbox_id"] = smtp_mailbox_id
        return get_client().post(f"/api/v1/campaigns/{campaign_id}/test-email", body)
