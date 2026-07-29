"""Campaign lifecycle, recipient metrics, and state reconciliation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from src.infra.models import (
    BackgroundTask,
    Campaign,
    CampaignBatch,
    CampaignRecipient,
    CampaignStatusEvent,
    DeliveryAttempt,
)


CAMPAIGN_STATUSES = frozenset(
    {
        "draft",
        "scheduled",
        "running",
        "paused",
        "completed",
        "completed_with_errors",
        "cancelled",
    }
)
ACTIVE_CAMPAIGN_STATUSES = frozenset({"scheduled", "running", "paused"})
TERMINAL_CAMPAIGN_STATUSES = frozenset({"completed", "completed_with_errors", "cancelled"})
ACTIVE_BATCH_STATUSES = frozenset({"pending", "running", "paused"})
ACTIVE_TASK_STATUSES = frozenset({"queued", "running", "retry"})
SUCCESS_RECIPIENT_STATUSES = frozenset({"sent", "in_chain"})
TERMINAL_RECIPIENT_STATUSES = frozenset({"sent", "in_chain", "skipped", "failed"})

ALLOWED_CAMPAIGN_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"scheduled", "running"}),
    "scheduled": frozenset(
        {"running", "paused", "completed", "completed_with_errors", "cancelled"}
    ),
    "running": frozenset({"paused", "completed", "completed_with_errors", "cancelled"}),
    "paused": frozenset(
        {"scheduled", "running", "completed", "completed_with_errors", "cancelled"}
    ),
    "completed": frozenset(),
    "completed_with_errors": frozenset(),
    "cancelled": frozenset(),
}


class CampaignStateConflict(ValueError):
    """The requested lifecycle action conflicts with the persisted campaign state."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def recipient_metrics_many(
    session: Any,
    campaigns: Iterable[Campaign],
) -> dict[str, dict[str, Any]]:
    rows = list(campaigns)
    campaign_ids = [str(row.id) for row in rows]
    status_counts: dict[str, dict[str, int]] = {campaign_id: {} for campaign_id in campaign_ids}
    if campaign_ids:
        grouped = session.execute(
            select(
                CampaignRecipient.campaign_id,
                CampaignRecipient.send_status,
                func.count(),
            )
            .where(CampaignRecipient.campaign_id.in_(campaign_ids))
            .group_by(CampaignRecipient.campaign_id, CampaignRecipient.send_status)
        ).all()
        for campaign_id, status, count in grouped:
            status_counts[str(campaign_id)][str(status or "pending")] = int(count or 0)

    attempt_counts: dict[str, int] = {campaign_id: 0 for campaign_id in campaign_ids}
    if campaign_ids:
        grouped_attempts = session.execute(
            select(DeliveryAttempt.campaign_id, func.count())
            .where(DeliveryAttempt.campaign_id.in_(campaign_ids))
            .group_by(DeliveryAttempt.campaign_id)
        ).all()
        for campaign_id, count in grouped_attempts:
            attempt_counts[str(campaign_id)] = int(count or 0)

    result: dict[str, dict[str, Any]] = {}
    for campaign in rows:
        counts = status_counts[str(campaign.id)]
        success_count = sum(counts.get(status, 0) for status in SUCCESS_RECIPIENT_STATUSES)
        skipped_count = counts.get("skipped", 0)
        failed_recipient_count = counts.get("failed", 0)
        processed_count = success_count + skipped_count + failed_recipient_count
        total_count = max(0, int(campaign.total_count or 0))
        pending_count = max(0, total_count - processed_count)
        progress = round(100.0 * processed_count / total_count, 1) if total_count else 0.0
        success_rate = round(100.0 * success_count / total_count, 1) if total_count else 0.0
        result[str(campaign.id)] = {
            "success_count": success_count,
            "skipped_count": skipped_count,
            "failed_recipient_count": failed_recipient_count,
            "processed_count": processed_count,
            "pending_count": pending_count,
            "attempt_count": attempt_counts[str(campaign.id)],
            "attempt_error_count": max(0, int(campaign.error_count or 0)),
            "progress": min(100.0, progress),
            "success_rate": min(100.0, success_rate),
            "recipient_status_counts": counts,
        }
    return result


def recipient_metrics(session: Any, campaign: Campaign) -> dict[str, Any]:
    return recipient_metrics_many(session, [campaign])[str(campaign.id)]


def active_work_counts(session: Any, campaign_id: str) -> dict[str, int]:
    batches = session.scalars(
        select(CampaignBatch).where(CampaignBatch.campaign_id == campaign_id)
    ).all()
    active_batches = sum(1 for batch in batches if batch.status in ACTIVE_BATCH_STATUSES)
    task_ids = [str(batch.task_id) for batch in batches if batch.task_id]
    active_tasks = 0
    if task_ids:
        active_tasks = int(
            session.scalar(
                select(func.count())
                .select_from(BackgroundTask)
                .where(
                    BackgroundTask.id.in_(task_ids),
                    BackgroundTask.status.in_(ACTIVE_TASK_STATUSES),
                )
            )
            or 0
        )
    return {"active_batches": active_batches, "active_tasks": active_tasks}


def terminal_status_for_metrics(metrics: dict[str, Any]) -> str | None:
    total_count = int(metrics.get("processed_count") or 0) + int(metrics.get("pending_count") or 0)
    if total_count <= 0 or int(metrics.get("pending_count") or 0) > 0:
        return None
    has_problems = (
        int(metrics.get("skipped_count") or 0) > 0
        or int(metrics.get("failed_recipient_count") or 0) > 0
    )
    return "completed_with_errors" if has_problems else "completed"


def allowed_actions(
    campaign: Campaign,
    metrics: dict[str, Any],
    *,
    has_active_work: bool | None = None,
) -> list[str]:
    status = str(campaign.status or "draft")
    actions = ["duplicate"]
    if status == "draft":
        return ["edit", "launch", *actions]
    if status in {"scheduled", "running"}:
        if has_active_work is False:
            return actions
        return ["pause", "cancel", *actions]
    if status == "paused":
        if has_active_work is not False and int(metrics.get("pending_count") or 0) > 0:
            return ["resume", "cancel", *actions]
        return ["cancel", *actions]
    if status in TERMINAL_CAMPAIGN_STATUSES:
        return [*actions, "archive"]
    return actions


def transition_campaign_status(
    session: Any,
    campaign: Campaign,
    target_status: str,
    *,
    reason: str,
    actor: str = "system",
    at: datetime | None = None,
) -> bool:
    current_status = str(campaign.status or "draft")
    target = str(target_status or "").strip()
    if target not in CAMPAIGN_STATUSES:
        raise CampaignStateConflict(f"Unknown campaign status: {target}")
    if current_status == target:
        return False
    if target not in ALLOWED_CAMPAIGN_TRANSITIONS.get(current_status, frozenset()):
        raise CampaignStateConflict(
            f"Campaign status cannot change from {current_status} to {target}"
        )

    changed_at = at or now_utc()
    session.add(
        CampaignStatusEvent(
            campaign_id=campaign.id,
            job_id=campaign.job_id,
            from_status=current_status,
            to_status=target,
            reason=str(reason or "")[:128],
            actor=str(actor or "system")[:64],
            details={
                "sent_count": int(campaign.sent_count or 0),
                "error_count": int(campaign.error_count or 0),
                "total_count": int(campaign.total_count or 0),
            },
        )
    )
    campaign.status = target
    campaign.updated_at = changed_at
    if target in TERMINAL_CAMPAIGN_STATUSES:
        campaign.completed_at = campaign.completed_at or changed_at
    return True


def inspect_campaign_state(session: Any, campaign: Campaign) -> dict[str, Any]:
    metrics = recipient_metrics(session, campaign)
    work = active_work_counts(session, campaign.id)
    has_active_work = bool(work["active_batches"] or work["active_tasks"])
    status = str(campaign.status or "draft")
    anomalies: list[str] = []
    target_status: str | None = None

    derived_terminal = terminal_status_for_metrics(metrics)
    if status in ACTIVE_CAMPAIGN_STATUSES and not has_active_work:
        if campaign.completed_at is not None:
            anomalies.append("active_status_with_completed_at")
        if derived_terminal:
            anomalies.append("active_status_without_active_work")
            target_status = derived_terminal
        elif int(metrics["pending_count"]) > 0:
            anomalies.append("active_status_without_work_with_pending_recipients")

    if status in TERMINAL_CAMPAIGN_STATUSES:
        if campaign.completed_at is None:
            anomalies.append("terminal_status_without_completed_at")
        if has_active_work:
            anomalies.append("terminal_status_with_active_work")
        if (
            status != "cancelled"
            and derived_terminal
            and derived_terminal != status
        ):
            anomalies.append("terminal_status_does_not_match_recipient_outcomes")
            target_status = derived_terminal

    if int(metrics["processed_count"]) > int(campaign.total_count or 0):
        anomalies.append("processed_count_exceeds_total_count")
    if int(campaign.sent_count or 0) != int(metrics["success_count"]):
        anomalies.append("sent_count_does_not_match_recipient_outcomes")

    return {
        "campaign_id": campaign.id,
        "status": status,
        "target_status": target_status,
        "anomalies": anomalies,
        "metrics": metrics,
        **work,
    }


def reconcile_campaign_state(
    session: Any,
    campaign: Campaign,
    *,
    repair: bool,
    actor: str = "reconciler",
) -> dict[str, Any]:
    report = inspect_campaign_state(session, campaign)
    if not repair:
        return report

    metrics = report["metrics"]
    campaign.sent_count = int(metrics["success_count"])
    target = report.get("target_status")
    if target and str(campaign.status) in ACTIVE_CAMPAIGN_STATUSES:
        transition_campaign_status(
            session,
            campaign,
            str(target),
            reason="state_reconciliation",
            actor=actor,
        )
    elif target and str(campaign.status) in {"completed", "completed_with_errors"}:
        current = str(campaign.status)
        session.add(
            CampaignStatusEvent(
                campaign_id=campaign.id,
                job_id=campaign.job_id,
                from_status=current,
                to_status=str(target),
                reason="state_reconciliation",
                actor=actor,
                details={
                    "sent_count": int(campaign.sent_count or 0),
                    "error_count": int(campaign.error_count or 0),
                    "total_count": int(campaign.total_count or 0),
                },
            )
        )
        campaign.status = str(target)
        campaign.updated_at = now_utc()
    if (
        str(campaign.status) in TERMINAL_CAMPAIGN_STATUSES
        and campaign.completed_at is None
    ):
        campaign.completed_at = now_utc()
    session.flush()
    return inspect_campaign_state(session, campaign)


def reconcile_inactive_campaigns(
    session: Any,
    *,
    repair: bool,
    actor: str = "campaign_state_reconciler",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Inspect active campaigns that no longer have an unfinished batch.

    A queued/running sender task still prevents terminalization in
    ``inspect_campaign_state``. Row locks make concurrent worker ticks harmless.
    """
    active_batch_exists = (
        select(CampaignBatch.id)
        .where(
            CampaignBatch.campaign_id == Campaign.id,
            CampaignBatch.status.in_(ACTIVE_BATCH_STATUSES),
        )
        .exists()
    )
    campaigns = session.scalars(
        select(Campaign)
        .where(
            Campaign.status.in_(ACTIVE_CAMPAIGN_STATUSES),
            ~active_batch_exists,
        )
        .order_by(Campaign.updated_at.asc())
        .limit(max(1, int(limit)))
        .with_for_update(skip_locked=True)
    ).all()

    reports: list[dict[str, Any]] = []
    for campaign in campaigns:
        before = inspect_campaign_state(session, campaign)
        if not before["anomalies"]:
            continue
        after = (
            reconcile_campaign_state(
                session,
                campaign,
                repair=True,
                actor=actor,
            )
            if repair
            else before
        )
        reports.append(
            {
                **after,
                "detected_anomalies": list(before["anomalies"]),
                "previous_status": before["status"],
            }
        )
    return reports
