from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.infra.db import session_scope
from src.infra.models import AgentState, BackgroundTask
from src.jobs.job_docs import LEGACY_JOB_ID
from src.jobs.storage import normalize_job_id


QUEUED = "queued"
RUNNING = "running"
RETRY = "retry"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE_STATUSES = (QUEUED, RUNNING, RETRY)
TERMINAL_STATUSES = (COMPLETED, FAILED, CANCELLED)
_QUEUE_STATUS_RE = re.compile(r"^worker-queue-([0-9a-fA-F-]{36})\.status\.json$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _storage_job_id(job_id: str | None) -> str:
    return normalize_job_id(job_id) or LEGACY_JOB_ID


def _as_dict(task: BackgroundTask) -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        return value.isoformat(timespec="seconds") if value is not None else None

    return {
        "id": str(task.id),
        "task_type": task.task_type,
        "job_id": None if task.job_id == LEGACY_JOB_ID else task.job_id,
        "owner_username": task.owner_username,
        "payload": dict(task.payload) if isinstance(task.payload, dict) else {},
        "result": dict(task.result) if isinstance(task.result, dict) else None,
        "status": task.status,
        "priority": task.priority,
        "attempt": task.attempt,
        "max_attempts": task.max_attempts,
        "available_at": iso(task.available_at),
        "lease_owner": task.lease_owner,
        "lease_expires_at": iso(task.lease_expires_at),
        "heartbeat_at": iso(task.heartbeat_at),
        "cancel_requested_at": iso(task.cancel_requested_at),
        "started_at": iso(task.started_at),
        "completed_at": iso(task.completed_at),
        "error": task.error,
        "created_at": iso(task.created_at),
        "updated_at": iso(task.updated_at),
    }


def _active_key(task_type: str, storage_job_id: str) -> str:
    return f"{task_type}:{storage_job_id}"


def enqueue_task(
    *,
    task_type: str,
    job_id: str | None,
    payload: dict[str, Any] | None = None,
    owner_username: str | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
    max_workers: int = 0,
    user_max_workers: int = 0,
) -> tuple[dict[str, Any], bool]:
    safe_task_type = str(task_type or "").strip()
    if not safe_task_type:
        raise ValueError("task_type is required")
    storage_job_id = _storage_job_id(job_id)
    safe_owner = str(owner_username or "").strip()
    active_key = _active_key(safe_task_type, storage_job_id)
    safe_idempotency_key = str(idempotency_key or "").strip() or None

    try:
        with session_scope() as session:
            existing = session.execute(
                select(BackgroundTask).where(BackgroundTask.active_key == active_key)
            ).scalar_one_or_none()
            if existing is not None:
                return _as_dict(existing), False

            if max_workers > 0:
                task_count = session.execute(
                    select(func.count())
                    .select_from(BackgroundTask)
                    .where(
                        BackgroundTask.task_type == safe_task_type,
                        BackgroundTask.status.in_(ACTIVE_STATUSES),
                    )
                ).scalar_one()
                if int(task_count) >= max_workers:
                    raise RuntimeError(
                        f"????????? ????? worker-????????? ??? ?????? {safe_task_type}: "
                        f"{task_count}/{max_workers}."
                    )

            if user_max_workers > 0 and safe_owner:
                user_count = session.execute(
                    select(func.count())
                    .select_from(BackgroundTask)
                    .where(
                        BackgroundTask.task_type == safe_task_type,
                        BackgroundTask.owner_username == safe_owner,
                        BackgroundTask.status.in_(ACTIVE_STATUSES),
                    )
                ).scalar_one()
                if int(user_count) >= user_max_workers:
                    raise RuntimeError(
                        f"????????? ????? worker-????????? ??? ???????????? {safe_owner}: "
                        f"{user_count}/{user_max_workers}."
                    )

            task = BackgroundTask(
                id=str(uuid4()),
                task_type=safe_task_type,
                job_id=storage_job_id,
                owner_username=safe_owner,
                payload=dict(payload or {}),
                status=QUEUED,
                priority=int(priority),
                attempt=0,
                max_attempts=max(1, int(max_attempts)),
                available_at=_now(),
                idempotency_key=safe_idempotency_key,
                active_key=active_key,
            )
            session.add(task)
            session.flush()
            return _as_dict(task), True
    except IntegrityError:
        with session_scope() as session:
            existing = session.execute(
                select(BackgroundTask).where(
                    (BackgroundTask.active_key == active_key)
                    | (
                        (BackgroundTask.idempotency_key == safe_idempotency_key)
                        if safe_idempotency_key is not None
                        else False
                    )
                )
            ).scalars().first()
            if existing is None:
                raise
            return _as_dict(existing), False


def get_task(task_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        task = session.get(BackgroundTask, str(task_id))
        return _as_dict(task) if task is not None else None


def get_task_payload(task_id: str) -> dict[str, Any]:
    task = get_task(task_id)
    if task is None:
        raise LookupError(f"background task {task_id} not found")
    return {
        "task": task["task_type"],
        "kwargs": dict(task.get("payload") or {}),
    }


def get_active_task(task_type: str, job_id: str | None) -> dict[str, Any] | None:
    active_key = _active_key(str(task_type or "").strip(), _storage_job_id(job_id))
    with session_scope() as session:
        task = session.execute(
            select(BackgroundTask).where(BackgroundTask.active_key == active_key)
        ).scalar_one_or_none()
        return _as_dict(task) if task is not None else None


def reconcile_orphaned_agent_states(*, grace_seconds: int = 120) -> int:
    expected_tasks = {
        "generator": {"generator", "documents"},
        "philologist": {"philologist", "documents"},
        "sender": {"sender"},
        "parser": {"parser_start", "parser_agent", "parser_verification"},
    }
    now = _now()
    cutoff = now - timedelta(seconds=max(0, int(grace_seconds)))
    reconciled = 0
    with session_scope() as session:
        states = session.execute(
            select(AgentState)
            .where(
                AgentState.agent_name.in_(tuple(expected_tasks)),
                AgentState.updated_at <= cutoff,
            )
            .with_for_update(skip_locked=True)
        ).scalars().all()
        for state_row in states:
            payload = dict(state_row.state) if isinstance(state_row.state, dict) else {}
            if str(payload.get("status") or "") not in {RUNNING, "finalizing"}:
                continue
            active_count = session.execute(
                select(func.count())
                .select_from(BackgroundTask)
                .where(
                    BackgroundTask.job_id == state_row.job_id,
                    BackgroundTask.task_type.in_(expected_tasks[state_row.agent_name]),
                    BackgroundTask.status.in_(ACTIVE_STATUSES),
                )
            ).scalar_one()
            if int(active_count) > 0:
                continue
            payload["status"] = "error"
            payload["completed_at"] = now.isoformat(timespec="seconds")
            payload["summary_text"] = (
                "The previous process stopped without a durable queue task. "
                "Start the operation again."
            )
            payload["recovered_after_restart"] = True
            state_row.state = payload
            state_row.updated_at = now
            reconciled += 1
    return reconciled



class QueueTaskHandle:
    def __init__(self, task_id: str, *, name: str | None = None) -> None:
        self.task_id = str(task_id)
        self.name = name or f"queue-{self.task_id}"

    def is_alive(self) -> bool:
        task = get_task(self.task_id)
        return bool(task and task.get("status") in ACTIVE_STATUSES)


def recover_expired_tasks(*, limit: int = 100) -> int:
    now = _now()
    recovered = 0
    with session_scope() as session:
        tasks = session.execute(
            select(BackgroundTask)
            .where(
                BackgroundTask.status == RUNNING,
                BackgroundTask.lease_expires_at.is_not(None),
                BackgroundTask.lease_expires_at < now,
            )
            .order_by(BackgroundTask.lease_expires_at.asc())
            .limit(max(1, int(limit)))
            .with_for_update(skip_locked=True)
        ).scalars().all()
        for task in tasks:
            task.lease_owner = None
            task.lease_expires_at = None
            task.heartbeat_at = None
            task.updated_at = now
            if task.cancel_requested_at is not None:
                task.status = CANCELLED
                task.completed_at = now
                task.active_key = None
            elif task.attempt >= task.max_attempts:
                task.status = FAILED
                task.completed_at = now
                task.error = task.error or "worker lease expired"
                task.active_key = None
            else:
                task.status = RETRY
                task.available_at = now
                task.error = "worker lease expired; task returned to queue"
            recovered += 1
    return recovered


def claim_task(*, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
    recover_expired_tasks()
    now = _now()
    with session_scope() as session:
        task = session.execute(
            select(BackgroundTask)
            .where(
                BackgroundTask.status.in_((QUEUED, RETRY)),
                BackgroundTask.available_at <= now,
                BackgroundTask.cancel_requested_at.is_(None),
            )
            .order_by(BackgroundTask.priority.desc(), BackgroundTask.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if task is None:
            return None

        task.status = RUNNING
        task.attempt += 1
        task.lease_owner = str(worker_id)
        task.lease_expires_at = now + timedelta(seconds=max(5, int(lease_seconds)))
        task.heartbeat_at = now
        task.started_at = task.started_at or now
        task.updated_at = now
        task.error = None
        session.flush()
        return _as_dict(task)


def heartbeat_task(*, task_id: str, worker_id: str, lease_seconds: int) -> bool:
    now = _now()
    with session_scope() as session:
        task = session.get(BackgroundTask, str(task_id))
        if task is None or task.status != RUNNING or task.lease_owner != str(worker_id):
            return False
        task.heartbeat_at = now
        task.lease_expires_at = now + timedelta(seconds=max(5, int(lease_seconds)))
        task.updated_at = now
        return True


def is_cancel_requested(task_id: str) -> bool:
    with session_scope() as session:
        task = session.get(BackgroundTask, str(task_id))
        return bool(task and task.cancel_requested_at is not None)


def request_cancel(task_id: str) -> dict[str, Any]:
    now = _now()
    with session_scope() as session:
        task = session.execute(
            select(BackgroundTask)
            .where(BackgroundTask.id == str(task_id))
            .with_for_update()
        ).scalar_one_or_none()
        if task is None:
            raise RuntimeError("???????? ?????? worker ?? ???????.")
        if task.status in TERMINAL_STATUSES:
            return _as_dict(task)
        task.cancel_requested_at = now
        task.updated_at = now
        if task.status in (QUEUED, RETRY):
            task.status = CANCELLED
            task.completed_at = now
            task.active_key = None
            task.lease_owner = None
            task.lease_expires_at = None
        session.flush()
        return _as_dict(task)


def mark_task_cancelled(*, task_id: str, worker_id: str) -> bool:
    now = _now()
    with session_scope() as session:
        task = session.get(BackgroundTask, str(task_id))
        if task is None or task.status != RUNNING or task.lease_owner != str(worker_id):
            return False
        task.status = CANCELLED
        task.completed_at = now
        task.active_key = None
        task.lease_owner = None
        task.lease_expires_at = None
        task.heartbeat_at = now
        task.updated_at = now
        return True


def complete_task(*, task_id: str, worker_id: str, result: dict[str, Any] | None = None) -> bool:
    now = _now()
    with session_scope() as session:
        task = session.get(BackgroundTask, str(task_id))
        if task is None or task.status != RUNNING or task.lease_owner != str(worker_id):
            return False
        task.status = COMPLETED
        task.result = dict(result or {})
        task.completed_at = now
        task.active_key = None
        task.lease_owner = None
        task.lease_expires_at = None
        task.heartbeat_at = now
        task.updated_at = now
        return True


def fail_task(
    *,
    task_id: str,
    worker_id: str,
    error: str,
    retry_base_seconds: int,
) -> str | None:
    now = _now()
    with session_scope() as session:
        task = session.get(BackgroundTask, str(task_id))
        if task is None or task.status != RUNNING or task.lease_owner != str(worker_id):
            return None

        task.error = str(error)
        task.lease_owner = None
        task.lease_expires_at = None
        task.heartbeat_at = now
        task.updated_at = now
        if task.cancel_requested_at is not None:
            task.status = CANCELLED
            task.completed_at = now
            task.active_key = None
        elif task.attempt < task.max_attempts:
            delay = max(1, int(retry_base_seconds)) * (2 ** max(0, task.attempt - 1))
            task.status = RETRY
            task.available_at = now + timedelta(seconds=delay)
        else:
            task.status = FAILED
            task.completed_at = now
            task.active_key = None
        return task.status


def task_id_from_status_path(status_path: str | None) -> str | None:
    name = Path(str(status_path or "")).name
    match = _QUEUE_STATUS_RE.fullmatch(name)
    return match.group(1) if match else None


def list_task_statuses(jobs_dir: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    with session_scope() as session:
        tasks = session.execute(
            select(BackgroundTask)
            .order_by(BackgroundTask.created_at.desc())
            .limit(max(1, int(limit)))
        ).scalars().all()

        statuses: list[dict[str, Any]] = []
        for task in tasks:
            job_id = None if task.job_id == LEGACY_JOB_ID else task.job_id
            status_path = jobs_dir / (job_id or LEGACY_JOB_ID) / "state" / f"worker-queue-{task.id}.status.json"
            statuses.append(
                {
                    "task": task.task_type,
                    "task_id": str(task.id),
                    "job_id": job_id,
                    "status": task.status,
                    "status_path": str(status_path),
                    "pid": None,
                    "return_code": None,
                    "message": task.error,
                    "started_at": task.started_at.isoformat(timespec="seconds") if task.started_at else None,
                    "updated_at": task.updated_at.isoformat(timespec="seconds"),
                    "completed_at": task.completed_at.isoformat(timespec="seconds") if task.completed_at else None,
                    "worker_id": task.lease_owner or str(task.id),
                    "owner_username": task.owner_username,
                    "attempt": task.attempt,
                    "max_attempts": task.max_attempts,
                    "alive": task.status in ACTIVE_STATUSES,
                }
            )
        return statuses
