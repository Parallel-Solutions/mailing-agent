from __future__ import annotations

import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.jobs import resolve_job_paths
from src.utils.config import settings
from src.utils.logger import logger
from src.workers.process_manager import _run_worker_process_monitor, _write_worker_payload
from src.workers.task_queue import (
    TaskRecord,
    claim_next_task,
    complete_task,
    fail_task,
    heartbeat_task,
    reconcile_expired_leases,
)


_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_worker_thread_lock = threading.Lock()


def request_queue_worker_stop() -> None:
    _stop_event.set()


def _job_state_dir(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).state_dir


def _mark_sender_failed(task: str, job_id: str | None, message: str) -> None:
    from src.generator.delivery.sender_agent import _load_sender_state, _save_sender_state

    if task != "sender":
        return
    state = _load_sender_state(job_id)
    state["status"] = "error"
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state["summary_text"] = f"Агент-отправщик остановился с ошибкой: {message}"
    _save_sender_state(state, job_id)


def _prime_sender_task_state(task: TaskRecord) -> None:
    payload = task.payload if isinstance(task.payload, dict) else {}
    kwargs = payload.get("kwargs") if isinstance(payload.get("kwargs"), dict) else payload
    dry_run = bool(kwargs.get("dry_run", False))
    job_id = task.job_id
    transport = kwargs.get("transport")
    attachment_mode = kwargs.get("attachment_mode")
    recipient_strategy = kwargs.get("recipient_strategy")
    sender_email = kwargs.get("sender_email")
    campaign_name = kwargs.get("campaign_name")
    from src.web.sender_service import prime_sender_checking_state, prime_sender_running_state

    if dry_run:
        prime_sender_checking_state(
            job_id,
            transport,
            attachment_mode,
            recipient_strategy,
            sender_email,
            campaign_name,
        )
    else:
        prime_sender_running_state(
            job_id,
            transport,
            attachment_mode,
            recipient_strategy,
            sender_email,
            campaign_name,
        )


def _run_sender_payload(task: TaskRecord, *, project_root: Path, worker_id: str) -> None:
    payload = task.payload if isinstance(task.payload, dict) else {}
    kwargs = payload.get("kwargs") if isinstance(payload.get("kwargs"), dict) else payload
    job_id = task.job_id
    if job_id:
        try:
            from src.jobs.workspace import pull_job

            pull_job(job_id, ["input", "templates", "output"])
        except ValueError:
            pass

    payload_path = _write_worker_payload(job_id, "sender", kwargs, _job_state_dir)
    status_path = payload_path.with_suffix(".status.json")
    timeout_seconds = max(0, int(settings.sender_worker_timeout_seconds or 0))
    _run_worker_process_monitor(
        task="sender",
        payload_path=payload_path,
        status_path=status_path,
        job_id=job_id,
        project_root=project_root,
        unregister=lambda _job_id: None,
        mark_failed=_mark_sender_failed,
        logger=logger,
        timeout_seconds=timeout_seconds,
    )


def _process_task(task: TaskRecord, *, project_root: Path, worker_id: str) -> None:
    task_type = task.task_type
    if task_type == "sender":
        from src.generator.delivery.send_guard import assert_sending_allowed

        assert_sending_allowed()
        _prime_sender_task_state(task)
        _run_sender_payload(task, project_root=project_root, worker_id=worker_id)
        return
    raise RuntimeError(f"unsupported queued task type: {task_type}")


def _queue_worker_loop(*, project_root: Path) -> None:
    worker_id = f"queue-worker-{secrets.token_hex(6)}"
    poll_seconds = max(0.1, float(settings.background_queue_poll_seconds or 1.0))
    lease_seconds = max(
        int(settings.background_queue_heartbeat_seconds or 30) * 2,
        int(settings.background_queue_lease_seconds or 7200),
    )
    heartbeat_seconds = max(1, int(settings.background_queue_heartbeat_seconds or 30))
    logger.info("queue_worker_started", worker_id=worker_id)
    while not _stop_event.is_set():
        try:
            reconcile_expired_leases(task_type="sender")
            from src.generator.delivery.send_guard import is_sending_paused

            if is_sending_paused():
                _stop_event.wait(poll_seconds)
                continue

            task = claim_next_task(task_type="sender", worker_id=worker_id, lease_seconds=lease_seconds)
            if task is None:
                _stop_event.wait(poll_seconds)
                continue

            logger.info("queue_task_started", task_id=task.id, task_type=task.task_type, job_id=task.job_id)
            try:
                monitor_stop = threading.Event()

                def _heartbeat_loop() -> None:
                    while not monitor_stop.wait(heartbeat_seconds):
                        if not heartbeat_task(task.id, worker_id=worker_id, lease_seconds=lease_seconds):
                            return

                heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
                heartbeat_thread.start()
                try:
                    _process_task(task, project_root=project_root, worker_id=worker_id)
                finally:
                    monitor_stop.set()
                    heartbeat_thread.join(timeout=heartbeat_seconds + 1)

                complete_task(task.id, worker_id=worker_id)
                logger.info("queue_task_completed", task_id=task.id, task_type=task.task_type, job_id=task.job_id)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                logger.exception("queue_task_failed", task_id=task.id, task_type=task.task_type, job_id=task.job_id)
                fail_task(task.id, worker_id=worker_id, error=message, retry=True)
        except Exception:
            logger.exception("queue_worker_loop_failed")
            _stop_event.wait(poll_seconds)
    logger.info("queue_worker_stopped", worker_id=worker_id)


def start_queue_worker(*, project_root: Path) -> None:
    with _worker_thread_lock:
        global _worker_thread
        if _worker_thread and _worker_thread.is_alive():
            return
        _stop_event.clear()
        _worker_thread = threading.Thread(
            target=_queue_worker_loop,
            kwargs={"project_root": project_root},
            daemon=True,
            name="sender-queue-worker",
        )
        _worker_thread.start()


def stop_queue_worker() -> None:
    request_queue_worker_stop()
    with _worker_thread_lock:
        global _worker_thread
        if _worker_thread and _worker_thread.is_alive():
            _worker_thread.join(timeout=max(1, int(settings.background_queue_shutdown_grace_seconds or 30)))
        _worker_thread = None


def enqueue_sender_task(
    *,
    job_id: str | None,
    owner_username: str,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    from src.workers.task_queue import enqueue_task, get_queue_snapshot

    task, created = enqueue_task(
        task_type="sender",
        job_id=job_id,
        owner_username=owner_username,
        payload={"kwargs": kwargs},
    )
    snapshot = get_queue_snapshot(task_type="sender", job_id=job_id)
    return {
        "task_id": task.id,
        "created": created,
        "status": task.status,
        "queue_position": snapshot.get("job_queue_position"),
        "queue_total": snapshot.get("total_active"),
        "queued_count": snapshot.get("queued_count"),
        "running_count": snapshot.get("running_count"),
    }
