"""CLI healthcheck for the queue worker container.

Exits 0 when the worker heartbeat is fresh and DB + Redis are reachable.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from src.infra.readiness import check_database, check_redis

HEARTBEAT_PATH = Path("/tmp/mailing_agent_queue_worker.heartbeat")
# Compose health interval is 15s; allow a few missed polls plus init_db work.
MAX_HEARTBEAT_AGE_SECONDS = 60.0


def touch_heartbeat(path: Path = HEARTBEAT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")


def check_heartbeat(path: Path = HEARTBEAT_PATH, *, max_age_seconds: float = MAX_HEARTBEAT_AGE_SECONDS) -> None:
    if not path.is_file():
        raise RuntimeError("worker heartbeat missing")
    age = time.time() - path.stat().st_mtime
    if age > max_age_seconds:
        raise RuntimeError(f"worker heartbeat stale ({age:.0f}s)")


def run_healthcheck() -> None:
    check_heartbeat()
    check_database()
    check_redis()


def main() -> int:
    try:
        run_healthcheck()
    except Exception as exc:  # noqa: BLE001 - healthcheck CLI must print a short reason
        print(f"worker unhealthy: {exc}", file=sys.stderr)
        return 1
    print("worker healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
