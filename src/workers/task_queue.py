from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.infra.db import session_scope
from src.infra.models import BackgroundTask
from src.utils.config import settings


ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TaskRecord:
    id: int
    task_type: str
    job_id: str | None
    owner_username: str
    payload: dict[str, Any]
    status: str
    priority: int
    worker_id: str | None
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    lease_expires_at: datetime | None

    @classmethod
    def from_model(cls, row: BackgroundTask) -> TaskRecord:
        return cls(
            id=int(row.id),
            task_type=str(row.task_type),
            job_id=str(row.job_id) if row.job_id else None,
            owner_username=str(row.owner_username or ""),
            payload=dict(row.payload or {}),
            status=str(row.status),
            priority=int(row.priority or 0),
            worker_id=str(row.worker_id) if row.worker_id else None,
            attempts=int(row.attempts or 0),
            max_attempts=int(row.max_attempts or 3),
            last_error=str(row.last_error) if row.last_error else None,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            lease_expires_at=row.lease_expires_at,
        )


def _find_active_task(session: Session, *, task_type: str, job_id: str | None) -> BackgroundTask | None:
    safe_job_id = str(job_id or "").strip() or None
    query = select(BackgroundTask).where(
        BackgroundTask.task_type == task_type,
        BackgroundTask.status.in_(ACTIVE_STATUSES),
    )
    if safe_job_id:
        query = query.where(BackgroundTask.job_id == safe_job_id)
    else:
        query = query.where(BackgroundTask.job_id.is_(None))
    return session.execute(query.order_by(BackgroundTask.created_at.desc()).limit(1)).scalar_one_or_none()


def enqueue_task(
    *,
    task_type: str,
    job_id: str | None,
    owner_username: str = "",
    payload: dict[str, Any] | None = None,
    priority: int = 0,
) -> tuple[TaskRecord, bool]:
    safe_type = str(task_type or "").strip()
    if not safe_type:
        raise ValueError("task_type is required")
    safe_job_id = str(job_id or "").strip() or None
    safe_owner = str(owner_username or "").strip()
    max_attempts = max(1, int(settings.background_queue_max_attempts or 3))

    with session_scope() as session:
        existing = _find_active_task(session, task_type=safe_type, job_id=safe_job_id)
        if existing is not None:
            return TaskRecord.from_model(existing), False

        row = BackgroundTask(
            task_type=safe_type,
            job_id=safe_job_id,
            owner_username=safe_owner,
            payload=dict(payload or {}),
            status="queued",
            priority=int(priority or 0),
            attempts=0,
            max_attempts=max_attempts,
            created_at=_now(),
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return TaskRecord.from_model(row), True


def claim_next_task(*, task_type: str, worker_id: str, lease_seconds: int) -> TaskRecord | None:
    safe_type = str(task_type or "").strip()
    safe_worker = str(worker_id or "").strip()
    if not safe_type or not safe_worker:
        return None
    lease = max(30, int(lease_seconds or 60))
    now = _now()
    expires = now + timedelta(seconds=lease)

    with session_scope() as session:
        row = session.execute(
            select(BackgroundTask)
            .where(
                BackgroundTask.task_type == safe_type,
                BackgroundTask.status == "queued",
            )
            .order_by(BackgroundTask.priority.desc(), BackgroundTask.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.worker_id = safe_worker
        row.started_at = now
        row.lease_expires_at = expires
        row.attempts = int(row.attempts or 0) + 1
        session.flush()
        session.refresh(row)
        return TaskRecord.from_model(row)


def heartbeat_task(task_id: int, *, worker_id: str, lease_seconds: int) -> bool:
    safe_worker = str(worker_id or "").strip()
    lease = max(30, int(lease_seconds or 60))
    now = _now()
    expires = now + timedelta(seconds=lease)
    with session_scope() as session:
        row = session.get(BackgroundTask, int(task_id))
        if row is None or row.status != "running" or str(row.worker_id or "") != safe_worker:
            return False
        row.lease_expires_at = expires
        return True


def complete_task(task_id: int, *, worker_id: str) -> bool:
    safe_worker = str(worker_id or "").strip()
    now = _now()
    with session_scope() as session:
        row = session.get(BackgroundTask, int(task_id))
        if row is None or row.status != "running" or str(row.worker_id or "") != safe_worker:
            return False
        row.status = "completed"
        row.finished_at = now
        row.lease_expires_at = None
        return True


def fail_task(task_id: int, *, worker_id: str, error: str, retry: bool = True) -> bool:
    safe_worker = str(worker_id or "").strip()
    safe_error = str(error or "").strip() or "unknown error"
    now = _now()
    with session_scope() as session:
        row = session.get(BackgroundTask, int(task_id))
        if row is None or row.status != "running" or str(row.worker_id or "") != safe_worker:
            return False
        row.last_error = safe_error
        row.lease_expires_at = None
        row.worker_id = None
        if retry and int(row.attempts or 0) < int(row.max_attempts or 1):
            row.status = "queued"
            row.started_at = None
        else:
            row.status = "failed"
            row.finished_at = now
        return True


def cancel_queued_task(*, task_type: str, job_id: str | None) -> bool:
    safe_type = str(task_type or "").strip()
    safe_job_id = str(job_id or "").strip() or None
    now = _now()
    with session_scope() as session:
        row = _find_active_task(session, task_type=safe_type, job_id=safe_job_id)
        if row is None or row.status != "queued":
            return False
        row.status = "cancelled"
        row.finished_at = now
        return True


def get_task_by_id(task_id: int) -> TaskRecord | None:
    with session_scope() as session:
        row = session.get(BackgroundTask, int(task_id))
        if row is None:
            return None
        return TaskRecord.from_model(row)


def get_queue_position(task_id: int, *, task_type: str = "sender") -> int | None:
    safe_type = str(task_type or "").strip()
    with session_scope() as session:
        target = session.get(BackgroundTask, int(task_id))
        if target is None or target.status not in ACTIVE_STATUSES:
            return None
        if target.status == "running":
            return 1
        earlier = session.execute(
            select(BackgroundTask.id).where(
                BackgroundTask.task_type == safe_type,
                BackgroundTask.status.in_(ACTIVE_STATUSES),
                BackgroundTask.created_at < target.created_at,
            )
        ).all()
        return len(earlier) + 1


def count_active_tasks(*, task_type: str = "sender") -> int:
    safe_type = str(task_type or "").strip()
    with session_scope() as session:
        rows = session.execute(
            select(BackgroundTask.id).where(
                BackgroundTask.task_type == safe_type,
                BackgroundTask.status.in_(ACTIVE_STATUSES),
            )
        ).all()
        return len(rows)


def list_active_tasks(*, task_type: str = "sender", limit: int = 50) -> list[TaskRecord]:
    safe_type = str(task_type or "").strip()
    safe_limit = max(1, min(int(limit or 50), 200))
    with session_scope() as session:
        rows = session.execute(
            select(BackgroundTask)
            .where(
                BackgroundTask.task_type == safe_type,
                BackgroundTask.status.in_(ACTIVE_STATUSES),
            )
            .order_by(BackgroundTask.priority.desc(), BackgroundTask.created_at.asc())
            .limit(safe_limit)
        ).scalars()
        return [TaskRecord.from_model(row) for row in rows]


def get_queue_snapshot(*, task_type: str = "sender", job_id: str | None = None) -> dict[str, Any]:
    safe_type = str(task_type or "").strip()
    safe_job_id = str(job_id or "").strip() or None
    active = list_active_tasks(task_type=safe_type)
    running = [item for item in active if item.status == "running"]
    queued = [item for item in active if item.status == "queued"]
    job_task = None
    job_position = None
    if safe_job_id:
        for index, item in enumerate(active, start=1):
            if item.job_id == safe_job_id:
                job_task = item
                job_position = index
                break
    return {
        "task_type": safe_type,
        "running_count": len(running),
        "queued_count": len(queued),
        "total_active": len(active),
        "running": [
            {
                "task_id": item.id,
                "job_id": item.job_id,
                "owner_username": item.owner_username,
                "status": item.status,
                "started_at": item.started_at.isoformat() if item.started_at else "",
            }
            for item in running
        ],
        "queued": [
            {
                "task_id": item.id,
                "job_id": item.job_id,
                "owner_username": item.owner_username,
                "status": item.status,
                "created_at": item.created_at.isoformat() if item.created_at else "",
            }
            for item in queued
        ],
        "job_id": safe_job_id,
        "job_task_id": job_task.id if job_task else None,
        "job_queue_position": job_position,
        "job_status": job_task.status if job_task else "",
    }


def reconcile_expired_leases(*, task_type: str = "sender") -> int:
    safe_type = str(task_type or "").strip()
    now = _now()
    count = 0
    with session_scope() as session:
        rows = session.execute(
            select(BackgroundTask).where(
                BackgroundTask.task_type == safe_type,
                BackgroundTask.status == "running",
                BackgroundTask.lease_expires_at.is_not(None),
                BackgroundTask.lease_expires_at < now,
            )
        ).scalars()
        for row in rows:
            row.last_error = "worker lease expired"
            row.worker_id = None
            row.lease_expires_at = None
            if int(row.attempts or 0) < int(row.max_attempts or 1):
                row.status = "queued"
                row.started_at = None
            else:
                row.status = "failed"
                row.finished_at = now
            count += 1
    return count
