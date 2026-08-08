from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Any
from uuid import uuid4

from src.infra.db import init_db
from src.jobs.workspace import pull_job, push_job
from src.utils.config import (
    SecurityConfigurationError,
    settings,
    validate_public_base_url,
    validate_runtime_database,
)
from src.utils.logger import logger
from src.workers.healthcheck import touch_heartbeat
from src.workers.task_queue import (
    reconcile_orphaned_agent_states,
    FAILED,
    claim_task,
    complete_task,
    fail_task,
    heartbeat_task,
    is_cancel_requested,
    mark_task_cancelled,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STOP_REQUESTED = False


def _request_stop(signum: int, frame: FrameType | None) -> None:
    del frame
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    logger.info("queue_worker_stop_requested", signal=signum)


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=max(1, int(settings.background_queue_shutdown_grace_seconds)))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _task_timeout_seconds(task_type: str) -> int:
    if task_type == "documents":
        return max(0, int(settings.documents_worker_timeout_seconds or 0))
    if task_type == "template_text_extract":
        return max(1, int(settings.template_text_extraction_timeout_seconds or 120))
    if task_type in {
        "sender",
        "sender_batch",
        "chain_followup",
        "recipient_resend",
        "campaign_pre_generate",
        "connection_warmup",
        "connection_sender_warmup",
        "connection_sender_warmup_message",
        "email_validation",
    }:
        return max(0, int(settings.sender_worker_timeout_seconds or 0))
    return 0


def _mark_terminal_failure(task: dict[str, Any], message: str) -> None:
    task_type = str(task.get("task_type") or "")
    if task_type == "template_text_extract":
        try:
            from src.campaigns.template_text_cache_service import mark_template_text_extraction_failed

            mark_template_text_extraction_failed(
                str((task.get("payload") or {}).get("version_id") or ""),
                message,
            )
        except Exception:
            logger.exception("queue_worker_finalize_template_text_extraction_failed", task_id=task.get("id"))
        return
    if task_type == "email_validation":
        try:
            from src.campaigns.email_validation_service import mark_validation_run_failed

            mark_validation_run_failed(
                str((task.get("payload") or {}).get("run_id") or ""),
                message,
            )
        except Exception:
            logger.exception(
                "queue_worker_finalize_email_validation_failed",
                task_id=task.get("id"),
            )
        return
    if task_type == "connection_warmup":
        try:
            from src.generator.delivery.connection_warmup import (
                finalize_connection_warmup_failure,
            )

            finalize_connection_warmup_failure(
                str((task.get("payload") or {}).get("connection_id") or ""),
                message,
                str((task.get("payload") or {}).get("key_guard_id") or "") or None,
            )
        except Exception:
            logger.exception(
                "queue_worker_finalize_connection_warmup_failed",
                task_id=task.get("id"),
            )
        return
    if task_type == "sender_batch":
        try:
            from src.campaigns.batch_worker import finalize_sender_batch_task_failure

            finalize_sender_batch_task_failure(str(task["id"]), message)
        except Exception:
            logger.exception(
                "queue_worker_finalize_sender_batch_failed",
                task_id=task.get("id"),
                job_id=task.get("job_id"),
            )
        return
    try:
        from src.workers.background_worker import mark_task_state_failed

        mark_task_state_failed(
            str(task.get("task_type") or ""),
            str(task.get("job_id") or "").strip() or None,
            message,
        )
    except Exception:
        logger.exception(
            "queue_worker_mark_state_failed",
            task_id=task.get("id"),
            task_type=task.get("task_type"),
            job_id=task.get("job_id"),
        )


def _finish_failed(task: dict[str, Any], worker_id: str, message: str) -> None:
    status = fail_task(
        task_id=str(task["id"]),
        worker_id=worker_id,
        error=message,
        retry_base_seconds=max(1, int(settings.background_queue_retry_base_seconds)),
    )
    logger.error(
        "queue_task_failed",
        task_id=task["id"],
        task_type=task["task_type"],
        job_id=task.get("job_id"),
        status=status,
        error=message,
    )
    if status == FAILED:
        _mark_terminal_failure(task, message)


def _sync_workspace(job_id: str | None, *, pull: bool) -> None:
    if not job_id:
        return
    try:
        if pull:
            pull_job(job_id, ["input", "templates", "output"])
        else:
            push_job(job_id, ["output", "reports", "consents", "templates"])
    except ValueError:
        return
    except Exception:
        logger.exception("queue_worker_workspace_sync_failed", job_id=job_id, pull=pull)


def _run_claimed_task(task: dict[str, Any], worker_id: str) -> None:
    task_id = str(task["id"])
    task_type = str(task["task_type"])
    job_id = str(task.get("job_id") or "").strip() or None
    if task_type == "sender":
        from src.generator.delivery.send_guard import assert_sending_allowed

        assert_sending_allowed()
    timeout_seconds = _task_timeout_seconds(task_type)
    heartbeat_seconds = max(1, int(settings.background_queue_heartbeat_seconds))
    lease_seconds = max(heartbeat_seconds * 2, int(settings.background_queue_lease_seconds))
    started_monotonic = time.monotonic()
    process: subprocess.Popen[Any] | None = None

    _sync_workspace(job_id, pull=True)
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "src.workers.background_worker",
                "--task-id",
                task_id,
            ],
            cwd=str(PROJECT_ROOT),
        )
        logger.info(
            "queue_task_started",
            task_id=task_id,
            task_type=task_type,
            job_id=job_id,
            pid=process.pid,
            attempt=task.get("attempt"),
        )

        while process.poll() is None:
            try:
                process.wait(timeout=heartbeat_seconds)
            except subprocess.TimeoutExpired:
                pass

            # Long-running sender/document tasks execute in a child process.
            # Keep the container-level heartbeat fresh while the parent is
            # supervising that child, not only while polling for new tasks.
            touch_heartbeat()
            if process.poll() is not None:
                break
            if is_cancel_requested(task_id):
                _terminate_process(process)
                mark_task_cancelled(task_id=task_id, worker_id=worker_id)
                _mark_terminal_failure(task, "task cancelled")
                logger.info("queue_task_cancelled", task_id=task_id, task_type=task_type, job_id=job_id)
                return
            if _STOP_REQUESTED:
                _terminate_process(process)
                _finish_failed(task, worker_id, "queue worker is shutting down")
                return
            if timeout_seconds > 0 and time.monotonic() - started_monotonic > timeout_seconds:
                _terminate_process(process)
                _finish_failed(task, worker_id, f"worker process exceeded timeout {timeout_seconds} seconds")
                return
            if not heartbeat_task(task_id=task_id, worker_id=worker_id, lease_seconds=lease_seconds):
                _terminate_process(process)
                logger.error("queue_task_lease_lost", task_id=task_id, task_type=task_type, job_id=job_id)
                return

        return_code = int(process.returncode or 0)
        if return_code == 0:
            complete_task(
                task_id=task_id,
                worker_id=worker_id,
                result={"return_code": return_code, "pid": process.pid},
            )
            logger.info("queue_task_completed", task_id=task_id, task_type=task_type, job_id=job_id)
        else:
            _finish_failed(task, worker_id, f"worker process exited with code {return_code}")
    except Exception as exc:
        if process is not None:
            _terminate_process(process)
        _finish_failed(task, worker_id, f"worker process failed: {type(exc).__name__}: {exc}")
    finally:
        _sync_workspace(job_id, pull=False)


def _run_consent_recovery_if_due(last_run: float) -> float:
    if not bool(settings.consent_materials_recovery_enabled):
        return last_run
    interval = max(10, int(settings.consent_materials_recovery_poll_seconds or 60))
    now = time.monotonic()
    if now - last_run < interval:
        return last_run
    try:
        from src.web.consent_router import recover_pending_materials_dispatches

        result = recover_pending_materials_dispatches(
            limit=max(1, int(settings.consent_materials_recovery_batch_size or 25))
        )
        if any(int(result.get(key) or 0) for key in ("checked", "sent", "failed", "skipped")):
            logger.info("consent_materials_recovery_tick", **result)
    except Exception:
        logger.exception("consent_materials_recovery_failed")
    return now


def _run_campaign_state_reconciliation_if_due(last_run: float) -> float:
    interval = 60
    now = time.monotonic()
    if now - last_run < interval:
        return last_run
    try:
        from src.campaigns.state import reconcile_inactive_campaigns
        from src.infra.db import session_scope

        with session_scope() as session:
            reports = reconcile_inactive_campaigns(
                session,
                repair=True,
                actor="queue_worker_reconciler",
            )
        if reports:
            logger.warning(
                "queue_worker_campaign_states_reconciled",
                count=len(reports),
                campaign_ids=[str(report["campaign_id"]) for report in reports],
            )
    except Exception:
        logger.exception("queue_worker_campaign_state_reconciliation_failed")
    return now


def _run_template_text_backfill_if_due(last_run: float) -> float:
    interval = 30
    now = time.monotonic()
    if now - last_run < interval:
        return last_run
    try:
        from src.campaigns.template_text_cache_service import enqueue_pending_template_text_extractions

        created = enqueue_pending_template_text_extractions(limit=10)
        if created:
            logger.info("template_source_text_backfill_enqueued", count=created)
    except Exception:
        logger.exception("template_source_text_backfill_enqueue_failed")
    return now


def main() -> None:
    signal.signal(signal.SIGTERM, _request_stop)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _request_stop)

    validate_public_base_url(settings)
    validate_runtime_database(settings)
    init_db()
    reconciled = reconcile_orphaned_agent_states()
    if reconciled:
        logger.warning("queue_worker_orphaned_states_reconciled", count=reconciled)

    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    poll_seconds = max(0.1, float(settings.background_queue_poll_seconds or 1.0))
    lease_seconds = max(
        int(settings.background_queue_heartbeat_seconds) * 2,
        int(settings.background_queue_lease_seconds),
    )
    last_consent_recovery = 0.0
    last_campaign_reconciliation = 0.0
    last_template_text_backfill = 0.0
    logger.info("queue_worker_started", worker_id=worker_id)
    last_orphan_reconciliation = time.monotonic()
    touch_heartbeat()

    while not _STOP_REQUESTED:
        touch_heartbeat()
        last_consent_recovery = _run_consent_recovery_if_due(last_consent_recovery)
        last_campaign_reconciliation = _run_campaign_state_reconciliation_if_due(
            last_campaign_reconciliation
        )
        last_template_text_backfill = _run_template_text_backfill_if_due(last_template_text_backfill)
        # When sending is auto-paused (e.g. an API error-rate spike), only
        # actual sending must stop. Previously this skipped claim_task
        # entirely, so a global send pause silently froze every task type on
        # the shared worker, including the unrelated SMTP.BZ recipient
        # validation check (which only calls an HTTP verification API and
        # never sends mail). Everything else — sender, documents, warmups,
        # etc. — keeps its existing paused-while-sending-is-paused behavior.
        only_task_types: set[str] | None = None
        try:
            from src.generator.delivery.send_guard import is_sending_paused

            if is_sending_paused():
                only_task_types = {"email_validation"}
        except Exception:
            logger.exception("queue_worker_send_guard_check_failed")
        task = claim_task(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            only_task_types=only_task_types,
        )
        now = time.monotonic()
        if now - last_orphan_reconciliation >= 60:
            reconciled = reconcile_orphaned_agent_states()
            if reconciled:
                logger.warning("queue_worker_orphaned_states_reconciled", count=reconciled)
            last_orphan_reconciliation = now

        if task is None:
            time.sleep(poll_seconds)
            continue
        _run_claimed_task(task, worker_id)

    logger.info("queue_worker_stopped", worker_id=worker_id)


def enqueue_sender_task(
    *,
    job_id: str | None,
    owner_username: str,
    kwargs: dict[str, Any],
    available_at: datetime | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    from src.workers.task_queue import enqueue_task, get_queue_snapshot

    safe_available_at = available_at
    if safe_available_at is not None and safe_available_at.tzinfo is None:
        safe_available_at = safe_available_at.replace(tzinfo=timezone.utc)

    task, created = enqueue_task(
        task_type="sender",
        job_id=job_id,
        owner_username=owner_username,
        # Flat kwargs — get_task_payload wraps payload as worker kwargs.
        # Nested {"kwargs": {...}} made dry_run/job_id invisible to _run_sender.
        payload=dict(kwargs or {}),
        max_workers=max(1, int(settings.sender_worker_max_processes or 1)),
        max_attempts=max(1, int(settings.background_queue_max_attempts or 3)),
        available_at=safe_available_at,
    )
    snapshot = get_queue_snapshot(task_type="sender", job_id=job_id)
    return {
        "task_id": task["id"],
        "created": created,
        "status": task["status"],
        "queue_position": snapshot.get("job_queue_position"),
        "queue_total": snapshot.get("total_active"),
        "queued_count": snapshot.get("queued_count"),
        "running_count": snapshot.get("running_count"),
    }


def start_queue_worker(*, project_root: Path) -> None:
    del project_root


def stop_queue_worker() -> None:
    return None


if __name__ == "__main__":
    main()
