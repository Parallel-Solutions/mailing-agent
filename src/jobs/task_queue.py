from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .storage import BASE_DIR


QUEUE_DB_PATH = BASE_DIR / "storage" / "task_queue.sqlite"
DEFAULT_STALE_AFTER_SECONDS = 10 * 60
DEFAULT_WAIT_POLL_SECONDS = 1.0
DEFAULT_HEARTBEAT_SECONDS = 15.0


def _connect() -> sqlite3.Connection:
    QUEUE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(QUEUE_DB_PATH), timeout=10, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS background_task_slots (
            slot_id TEXT PRIMARY KEY,
            module TEXT NOT NULL,
            job_id TEXT,
            owner TEXT NOT NULL,
            pid INTEGER NOT NULL,
            thread_id TEXT NOT NULL,
            acquired_at REAL NOT NULL,
            heartbeat_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS background_task_waiting (
            waiter_id TEXT PRIMARY KEY,
            module TEXT NOT NULL,
            job_id TEXT,
            owner TEXT NOT NULL,
            pid INTEGER NOT NULL,
            thread_id TEXT NOT NULL,
            waiting_since REAL NOT NULL,
            heartbeat_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_background_task_slots_module ON background_task_slots(module)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_background_task_waiting_module ON background_task_waiting(module)")


def _cleanup_stale(conn: sqlite3.Connection, *, now: float, stale_after_seconds: float) -> None:
    stale_before = now - stale_after_seconds
    conn.execute("DELETE FROM background_task_slots WHERE heartbeat_at < ?", (stale_before,))
    conn.execute("DELETE FROM background_task_waiting WHERE heartbeat_at < ?", (stale_before,))


def _count_slots(conn: sqlite3.Connection, module: str) -> tuple[int, int]:
    total = int(conn.execute("SELECT COUNT(*) FROM background_task_slots").fetchone()[0] or 0)
    module_total = int(
        conn.execute("SELECT COUNT(*) FROM background_task_slots WHERE module = ?", (module,)).fetchone()[0] or 0
    )
    return total, module_total


def _upsert_waiter(conn: sqlite3.Connection, *, waiter_id: str, module: str, job_id: str | None, owner: str, now: float) -> None:
    conn.execute(
        """
        INSERT INTO background_task_waiting (
            waiter_id, module, job_id, owner, pid, thread_id, waiting_since, heartbeat_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(waiter_id) DO UPDATE SET
            module = excluded.module,
            job_id = excluded.job_id,
            heartbeat_at = excluded.heartbeat_at
        """,
        (waiter_id, module, job_id, owner, os.getpid(), str(threading.get_ident()), now, now),
    )


def _release_owner(owner: str) -> None:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM background_task_slots WHERE owner = ?", (owner,))
        conn.execute("DELETE FROM background_task_waiting WHERE owner = ?", (owner,))
        conn.execute("COMMIT")


def _heartbeat_owner(owner: str, stop_event: threading.Event, interval_seconds: float) -> None:
    while not stop_event.wait(interval_seconds):
        now = time.time()
        try:
            with _connect() as conn:
                conn.execute(
                    "UPDATE background_task_slots SET heartbeat_at = ? WHERE owner = ?",
                    (now, owner),
                )
        except Exception:
            # The worker should not fail just because the diagnostic queue failed.
            continue


@contextmanager
def persistent_task_slot(
    *,
    module: str,
    job_id: str | None,
    global_limit: int,
    module_limits: dict[str, int],
    on_wait: Callable[[], None] | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    wait_poll_seconds: float = DEFAULT_WAIT_POLL_SECONDS,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
) -> Iterator[None]:
    owner = uuid.uuid4().hex
    waiter_id = owner
    module_limit = max(1, int(module_limits.get(module, 1)))
    global_limit = max(1, int(global_limit))
    wait_announced = False
    stop_event = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    acquired = False

    try:
        while True:
            now = time.time()
            with _connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                _cleanup_stale(conn, now=now, stale_after_seconds=stale_after_seconds)
                total_slots, module_slots = _count_slots(conn, module)
                if total_slots < global_limit and module_slots < module_limit:
                    conn.execute("DELETE FROM background_task_waiting WHERE waiter_id = ?", (waiter_id,))
                    conn.execute(
                        """
                        INSERT INTO background_task_slots (
                            slot_id, module, job_id, owner, pid, thread_id, acquired_at, heartbeat_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            owner,
                            module,
                            job_id,
                            owner,
                            os.getpid(),
                            str(threading.get_ident()),
                            now,
                            now,
                        ),
                    )
                    conn.execute("COMMIT")
                    acquired = True
                    break

                _upsert_waiter(conn, waiter_id=waiter_id, module=module, job_id=job_id, owner=owner, now=now)
                conn.execute("COMMIT")

            if not wait_announced:
                wait_announced = True
                if on_wait is not None:
                    on_wait()
            time.sleep(wait_poll_seconds)

        heartbeat_thread = threading.Thread(
            target=_heartbeat_owner,
            kwargs={"owner": owner, "stop_event": stop_event, "interval_seconds": heartbeat_seconds},
            daemon=True,
            name=f"task-slot-heartbeat-{module}-{owner[:8]}",
        )
        heartbeat_thread.start()
        yield
    finally:
        stop_event.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)
        if acquired or wait_announced:
            _release_owner(owner)


def task_queue_snapshot(
    *,
    module_limits: dict[str, int],
    global_limit: int,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> dict:
    modules = sorted(module_limits)
    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _cleanup_stale(conn, now=now, stale_after_seconds=stale_after_seconds)
        conn.execute("COMMIT")
        usage = {}
        for module in modules:
            active = int(
                conn.execute("SELECT COUNT(*) FROM background_task_slots WHERE module = ?", (module,)).fetchone()[0]
                or 0
            )
            waiting = int(
                conn.execute("SELECT COUNT(*) FROM background_task_waiting WHERE module = ?", (module,)).fetchone()[0]
                or 0
            )
            usage[module] = {
                "limit": int(module_limits[module]),
                "active": active,
                "waiting": waiting,
            }
        total_active = int(conn.execute("SELECT COUNT(*) FROM background_task_slots").fetchone()[0] or 0)
        total_waiting = int(conn.execute("SELECT COUNT(*) FROM background_task_waiting").fetchone()[0] or 0)
    return {
        "db_path": str(QUEUE_DB_PATH),
        "global_limit": int(global_limit),
        "total_active": total_active,
        "total_waiting": total_waiting,
        "modules": usage,
    }
