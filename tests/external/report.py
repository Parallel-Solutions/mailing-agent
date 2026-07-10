"""Report writer for external statistics test results.

Produces JSON and Markdown reports that match the structure defined in
EXTERNAL_STATISTICS_TEST_PLAN.md § 6.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ExtTestResult:
    test_id: str               # EXT-SEND-01
    provider: str              # rusender | mailopost | unisender_go
    scenario: str              # human-readable description
    level: str                 # L1 | L2 | L3 | L4
    status: str = "pending"    # pending | pass | fail | skip
    detail: str = ""
    app_sent: int = 0
    app_delivered: int = 0
    app_opened: int = 0
    app_clicked: int = 0
    provider_message_id: str = ""
    webhook_delay_sec: float = 0.0
    mismatches: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


@dataclass
class ExtReport:
    job_id: str
    transport: str
    public_base_url: str
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    results: list[ExtTestResult] = field(default_factory=list)

    def add(self, result: ExtTestResult) -> None:
        self.results.append(result)

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.results),
            "pass": sum(1 for r in self.results if r.status == "pass"),
            "fail": sum(1 for r in self.results if r.status == "fail"),
            "skip": sum(1 for r in self.results if r.status == "skip"),
            "pending": sum(1 for r in self.results if r.status == "pending"),
        }

    def passed(self) -> bool:
        summary = self.summary()
        return summary["fail"] == 0 and summary["pass"] > 0


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_json(report: ExtReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"ext_stats_report_{ts}.json"
    payload = {
        "job_id": report.job_id,
        "transport": report.transport,
        "public_base_url": report.public_base_url,
        "started_at": report.started_at,
        "summary": report.summary(),
        "results": [asdict(r) for r in report.results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_markdown(report: ExtReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"ext_stats_report_{ts}.md"
    lines = _build_markdown(report)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _status_emoji(status: str) -> str:
    return {"pass": "✅", "fail": "❌", "skip": "⚠️", "pending": "⏳"}.get(status, "?")


def _build_markdown(report: ExtReport) -> list[str]:
    s = report.summary()
    lines: list[str] = [
        "# External Statistics Test Report",
        "",
        f"**Job ID:** `{report.job_id}`  ",
        f"**Transport:** `{report.transport}`  ",
        f"**Public URL:** `{report.public_base_url}`  ",
        f"**Started:** {report.started_at}  ",
        "",
        "## Summary",
        "",
        f"| Total | Pass | Fail | Skip | Pending |",
        f"|---:|---:|---:|---:|---:|",
        f"| {s['total']} | {s['pass']} | {s['fail']} | {s['skip']} | {s['pending']} |",
        "",
        "## Test Results",
        "",
        "| ID | Provider | Scenario | Level | Status | Provider Message ID | Webhook Delay | Detail |",
        "|---|---|---|---|---|---|---:|---|",
    ]

    for r in report.results:
        emoji = _status_emoji(r.status)
        delay = f"{r.webhook_delay_sec:.1f}s" if r.webhook_delay_sec else "—"
        mid = f"`{r.provider_message_id}`" if r.provider_message_id else "—"
        detail = r.detail.replace("|", "/")[:80]
        lines.append(
            f"| {r.test_id} | {r.provider} | {r.scenario} | {r.level} "
            f"| {emoji} {r.status} | {mid} | {delay} | {detail} |"
        )

    # Mismatches
    all_mismatches = [
        (r.test_id, m) for r in report.results for m in r.mismatches
    ]
    if all_mismatches:
        lines += [
            "",
            "## Mismatches",
            "",
            "| Test | Mismatch |",
            "|---|---|",
        ]
        for tid, m in all_mismatches:
            lines.append(f"| {tid} | {m} |")

    lines += [
        "",
        "## Known Gaps",
        "",
        "- SMTP transport: нет delivered/bounced без DSN/IMAP-обработчика.",
        "- Нет custom tracking pixel — open events только от провайдера.",
        "- Нет click proxy — click events только от провайдера.",
        "- `clicked_after_consent` захардкожен = 0.",
        "- Unsubscribe/spam не блокируют повторную отправку (нет системной блокировки).",
        "- UniSender Classic — только polling, задержка статуса.",
        "",
    ]
    return lines


def print_summary(report: ExtReport) -> None:
    s = report.summary()
    print(f"\n{'='*50}")
    print("External Statistics Tests Summary")
    print(f"{'='*50}")
    print(f"Total:   {s['total']}")
    print(f"Pass:    {s['pass']}")
    print(f"Fail:    {s['fail']}")
    print(f"Skip:    {s['skip']}")
    print(f"Pending: {s['pending']}")
    if s["fail"]:
        print("\nFailed tests:")
        for r in report.results:
            if r.status == "fail":
                print(f"  [{r.test_id}] {r.scenario}: {r.detail}")
    print(f"{'='*50}\n")
