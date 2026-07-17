from __future__ import annotations

from typing import Any, Callable


def _stats_params(
    *,
    period_from: str | None = None,
    period_to: str | None = None,
    job_id: str | None = None,
    q: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "period_from": period_from,
        "period_to": period_to,
        "job_id": job_id,
        "q": q,
        "limit": limit,
        "offset": offset,
    }
    if extra:
        params.update(extra)
    return params


def register_statistics_tools(mcp: Any, get_client: Callable[[], Any]) -> None:
    @mcp.tool()
    def get_manager_dashboard(
        period_from: str | None = None,
        period_to: str | None = None,
        job_id: str | None = None,
    ) -> Any:
        """Manager dashboard KPIs and charts (`/api/sender/manager-dashboard`)."""
        return get_client().get(
            "/api/sender/manager-dashboard",
            params=_stats_params(period_from=period_from, period_to=period_to, job_id=job_id),
        )

    @mcp.tool()
    def list_stats_campaigns(
        period_from: str | None = None,
        period_to: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """List campaigns in statistics view (`/api/sender/campaigns`)."""
        return get_client().get(
            "/api/sender/campaigns",
            params=_stats_params(
                period_from=period_from,
                period_to=period_to,
                q=q,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool()
    def list_stats_recipients(
        period_from: str | None = None,
        period_to: str | None = None,
        job_id: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """List recipients in statistics view."""
        return get_client().get(
            "/api/sender/recipients",
            params=_stats_params(
                period_from=period_from,
                period_to=period_to,
                job_id=job_id,
                q=q,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool()
    def get_stats_recipient(row_key: str) -> Any:
        """Get recipient detail by row_key."""
        return get_client().get(f"/api/sender/recipients/{row_key}")

    @mcp.tool()
    def save_stats_recipient_action(row_key: str, body: dict[str, Any]) -> Any:
        """Save a manager action on a recipient. Body: action_type, responsible_manager, due_at, comment, priority."""
        return get_client().post(f"/api/sender/recipients/{row_key}/action", body)

    @mcp.tool()
    def list_stats_consents(
        period_from: str | None = None,
        period_to: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """List consent records in statistics view."""
        return get_client().get(
            "/api/sender/consents",
            params=_stats_params(
                period_from=period_from,
                period_to=period_to,
                q=q,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool()
    def list_email_problems(
        period_from: str | None = None,
        period_to: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """List email delivery problems."""
        return get_client().get(
            "/api/sender/email-problems",
            params=_stats_params(
                period_from=period_from,
                period_to=period_to,
                q=q,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool()
    def get_campaign_analytics(
        job_id: str,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> Any:
        """Campaign analytics for a job_id."""
        return get_client().get(
            f"/api/sender/campaign-analytics/{job_id}",
            params=_stats_params(period_from=period_from, period_to=period_to),
        )

    @mcp.tool()
    def list_reports(
        period_from: str | None = None,
        period_to: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """List generated statistics reports."""
        return get_client().get(
            "/api/sender/reports",
            params=_stats_params(
                period_from=period_from,
                period_to=period_to,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool()
    def export_report(body: dict[str, Any]) -> Any:
        """Export a statistics report. Body: report_type, period_from, period_to, job_id, fmt, options."""
        return get_client().post("/api/sender/reports/export", body)

    @mcp.tool()
    def get_report_download_meta(report_id: str) -> Any:
        """Fetch report download metadata/bytes as base64 (`/api/sender/reports/download/{id}`)."""
        return get_client().request(
            "GET",
            f"/api/sender/reports/download/{report_id}",
            raw=True,
        )

    @mcp.tool()
    def get_domain_delivery_stats(
        period_from: str | None = None,
        period_to: str | None = None,
        job_id: str | None = None,
    ) -> Any:
        """Domain delivery statistics."""
        return get_client().get(
            "/api/sender/domain-delivery-stats",
            params=_stats_params(period_from=period_from, period_to=period_to, job_id=job_id),
        )
