#!/usr/bin/env python3
"""Re-attach unmatched RuSender/MailoPost webhook events to jobs.

Use after provider_message_id normalization so historical ``rusender:uuid``
log rows match bare webhook task/message ids.
"""

from __future__ import annotations

import argparse
import json

from src.generator.delivery.repair_unmatched_events import repair_unmatched_provider_events
from src.infra.db import init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair unmatched provider webhook events.")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not rewrite files")
    args = parser.parse_args(argv)
    init_db()
    report = repair_unmatched_provider_events(dry_run=bool(args.dry_run))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
