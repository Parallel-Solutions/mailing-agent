#!/usr/bin/env python3
"""Sync delivery events from RuSender/MailoPost APIs into job_events.

Requires provider message ids already present in sent_mail_log.
RuSender does not expose a bulk send-history list API; without local
sent_mail_log (or webhooks), full historical recovery is impossible.
"""

from __future__ import annotations

import argparse
import json

from src.generator.delivery.manager_stats import invalidate_stats_cache
from src.generator.delivery.provider_status_sync import sync_provider_delivery_events
from src.infra.db import init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull RuSender/MailoPost delivery events into PostgreSQL.")
    parser.add_argument("--job-id", default="", help="Optional single job id")
    parser.add_argument(
        "--providers",
        default="rusender,mailopost",
        help="Comma-separated provider list (default: rusender,mailopost)",
    )
    args = parser.parse_args(argv)
    init_db()
    providers = tuple(item.strip().lower() for item in str(args.providers).split(",") if item.strip())
    report = sync_provider_delivery_events(
        job_id=str(args.job_id or "").strip() or None,
        providers=providers or ("rusender", "mailopost"),
    )
    invalidate_stats_cache(str(args.job_id or "").strip() or None)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    counts = report.get("counts") or {}
    if not any(int(v or 0) for v in counts.values()):
        print(
            json.dumps(
                {
                    "warning": (
                        "В sent_mail_log нет provider_message_id для RuSender/MailoPost. "
                        "API провайдеров не отдаёт полный список истории отправок — "
                        "нужны webhook-и, бэкап JSONL или импорт логов с task_id."
                    )
                },
                ensure_ascii=False,
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
