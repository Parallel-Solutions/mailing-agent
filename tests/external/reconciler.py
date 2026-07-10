"""Reconciliation engine for external statistics tests (EXT-RECON-01).

Compares statistics from all four sources:
  1. Application API  (/api/sender/manager-dashboard, /campaigns, JSONL files)
  2. Provider API     (RuSender / MailoPost / UniSender Go via provider adapters)
  3. Database         (sent_mail_log from job_events PostgreSQL stream)
  4. Mailbox          (ImapMailboxAdapter — count of received emails)

Any stable discrepancy is recorded as a Mismatch with severity HIGH/MEDIUM/LOW.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ReconSource:
    """Snapshot of statistics from one source."""
    name: str              # "app_api" | "provider" | "db" | "mailbox" | "jsonl"
    sent: int = 0
    delivered: int = 0
    opened: int = 0
    clicked: int = 0
    hard_bounced: int = 0
    soft_bounced: int = 0
    unsubscribed: int = 0
    spam: int = 0
    failed: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Mismatch:
    metric: str            # "sent" | "delivered" | ...
    expected: int
    actual: int
    source_expected: str
    source_actual: str
    severity: str          # "HIGH" | "MEDIUM" | "LOW"
    note: str = ""

    def __str__(self) -> str:
        return (
            f"[{self.severity}] {self.metric}: "
            f"{self.source_expected}={self.expected} vs "
            f"{self.source_actual}={self.actual}"
            + (f" ({self.note})" if self.note else "")
        )


@dataclass
class ReconReport:
    sources: list[ReconSource] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)

    def passed(self) -> bool:
        return not any(m.severity == "HIGH" for m in self.mismatches)

    def summary_lines(self) -> list[str]:
        lines = []
        headers = ["Метрика"] + [s.name for s in self.sources]
        lines.append(" | ".join(headers))
        lines.append("-" * (len(" | ".join(headers))))
        for metric in ("sent", "delivered", "opened", "clicked", "hard_bounced", "soft_bounced", "unsubscribed", "spam"):
            values = [metric] + [str(getattr(s, metric)) for s in self.sources]
            lines.append(" | ".join(values))
        if self.mismatches:
            lines.append("")
            lines.append("Расхождения:")
            for m in self.mismatches:
                lines.append(f"  {m}")
        return lines


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------


class Reconciler:
    """Compare statistics across sources and produce a ReconReport."""

    # Metrics that matter for reconciliation (in priority order)
    METRICS = ("sent", "delivered", "opened", "clicked", "hard_bounced", "soft_bounced", "unsubscribed", "spam", "failed")

    def __init__(self, tolerance: int = 1) -> None:
        """tolerance: allowed absolute discrepancy before flagging HIGH severity."""
        self.tolerance = tolerance

    def compare(self, sources: list[ReconSource]) -> ReconReport:
        report = ReconReport(sources=sources)
        if len(sources) < 2:
            return report

        # Use the first source (app_api) as reference.
        ref = sources[0]
        for src in sources[1:]:
            for metric in self.METRICS:
                ref_val = getattr(ref, metric)
                src_val = getattr(src, metric)
                if abs(ref_val - src_val) > self.tolerance:
                    severity = self._severity(metric, ref_val, src_val)
                    report.mismatches.append(Mismatch(
                        metric=metric,
                        expected=ref_val,
                        actual=src_val,
                        source_expected=ref.name,
                        source_actual=src.name,
                        severity=severity,
                    ))
        return report

    def _severity(self, metric: str, ref: int, actual: int) -> str:
        if metric in ("sent", "delivered"):
            return "HIGH"
        if metric in ("hard_bounced", "soft_bounced", "spam"):
            return "HIGH"
        if metric in ("opened", "clicked"):
            return "MEDIUM"
        return "LOW"


# ---------------------------------------------------------------------------
# Builder helpers — construct ReconSource from various data shapes
# ---------------------------------------------------------------------------


def source_from_app_dashboard(data: dict[str, Any]) -> ReconSource:
    """Build ReconSource from /api/sender/manager-dashboard response."""
    summary = data.get("summary") or {}
    return ReconSource(
        name="app_api",
        sent=int(summary.get("sent") or 0),
        delivered=int(summary.get("delivered") or 0),
        opened=int(summary.get("opened") or 0),
        clicked=int(summary.get("clicked") or 0),
        hard_bounced=int(summary.get("hard_bounced") or 0),
        soft_bounced=int(summary.get("soft_bounced") or 0),
        unsubscribed=int(summary.get("unsubscribed") or 0),
        spam=int(summary.get("spam") or 0),
        failed=int(summary.get("failed") or summary.get("delivery_error") or 0),
        raw=data,
    )


def source_from_sent_mail_log(records: list[dict[str, Any]]) -> ReconSource:
    """Build ReconSource from sent_mail_log records (DB source)."""
    return ReconSource(
        name="db_sent_log",
        sent=len(records),
        raw={"records": len(records)},
    )


def source_from_jsonl_events(events: list[dict[str, Any]]) -> ReconSource:
    """Build ReconSource from provider JSONL events."""
    counts: dict[str, int] = {}
    for ev in events:
        status = str(ev.get("provider_status") or ev.get("event_type") or "")
        counts[status] = counts.get(status, 0) + 1

    def _sum(*keys: str) -> int:
        return sum(counts.get(k, 0) for k in keys)

    return ReconSource(
        name="jsonl_events",
        sent=_sum("sent"),
        delivered=_sum("delivered", "ok_delivered"),
        opened=_sum("opened", "ok_read"),
        clicked=_sum("clicked", "ok_link_visited"),
        hard_bounced=_sum("hard_bounced", "err_user_unknown", "err_user_inactive"),
        soft_bounced=_sum("soft_bounced", "err_will_retry"),
        unsubscribed=_sum("unsubscribed", "ok_unsubscribed"),
        spam=_sum("spam", "complaint"),
        failed=_sum("failed", "err_delivery_failed"),
        raw=dict(counts),
    )


def source_from_provider_events(events: list[Any]) -> ReconSource:
    """Build ReconSource from provider adapter events (ProviderEvent list)."""
    counts: dict[str, int] = {}
    for ev in events:
        key = str(getattr(ev, "event_type", "") or "").lower()
        counts[key] = counts.get(key, 0) + 1

    def _sum(*keys: str) -> int:
        return sum(counts.get(k, 0) for k in keys)

    return ReconSource(
        name="provider_api",
        delivered=_sum("delivered", "ok_delivered", "external_mail.delivered"),
        opened=_sum("opened", "ok_read", "external_mail.open"),
        clicked=_sum("clicked", "ok_link_visited", "external_mail.click"),
        hard_bounced=_sum("hard_bounced", "err_user_unknown", "external_mail.hard_bounced"),
        soft_bounced=_sum("soft_bounced", "external_mail.soft_bounced"),
        unsubscribed=_sum("unsubscribed", "external_mail.unsubscribe"),
        spam=_sum("spam", "complaint", "external_mail.complaint"),
        raw=dict(counts),
    )


def source_from_mailbox(message_count: int) -> ReconSource:
    """Build ReconSource from a mailbox message count."""
    return ReconSource(
        name="mailbox",
        sent=message_count,
        raw={"messages_found": message_count},
    )
