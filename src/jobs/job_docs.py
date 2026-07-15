from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from src.infra.db import session_scope
from src.infra.models import EventStreamCounter, JobDoc, JobEvent, JobOwner
from src.jobs.storage import normalize_job_id


LEGACY_JOB_ID = "__legacy__"


def _storage_job_id(job_id: str | None) -> str:
    normalized = normalize_job_id(job_id)
    return normalized or LEGACY_JOB_ID


def read_doc(job_id: str | None, name: str) -> dict[str, Any]:
    storage_job_id = _storage_job_id(job_id)
    with session_scope() as session:
        row = session.get(JobDoc, {"job_id": storage_job_id, "name": name})
    if row is None or not isinstance(row.payload, dict):
        return {}
    return dict(row.payload)


def write_doc(job_id: str | None, name: str, payload: dict[str, Any]) -> None:
    storage_job_id = _storage_job_id(job_id)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(JobDoc, {"job_id": storage_job_id, "name": name})
        if row is None:
            session.add(JobDoc(job_id=storage_job_id, name=name, payload=payload, updated_at=now))
        else:
            row.payload = payload
            row.updated_at = now


def delete_doc(job_id: str | None, name: str) -> None:
    storage_job_id = _storage_job_id(job_id)
    with session_scope() as session:
        row = session.get(JobDoc, {"job_id": storage_job_id, "name": name})
        if row is not None:
            session.delete(row)


def read_owner(job_id: str | None) -> dict[str, Any]:
    normalized = normalize_job_id(job_id)
    if not normalized:
        return {}
    with session_scope() as session:
        row = session.get(JobOwner, normalized)
    if row is None:
        return {}
    return {
        "job_id": row.job_id,
        "owner_username": row.owner_username,
        "tenant_id": row.tenant_id,
        "owner_role": row.owner_role,
        "created_at": row.created_at.isoformat(timespec="seconds"),
        "updated_at": row.updated_at.isoformat(timespec="seconds"),
    }


def write_owner(job_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_job_id(job_id)
    if not normalized:
        return {}
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(JobOwner, normalized)
        if row is None:
            row = JobOwner(
                job_id=normalized,
                owner_username=str(payload.get("owner_username") or ""),
                tenant_id=str(payload.get("tenant_id") or ""),
                owner_role=str(payload.get("owner_role") or "user"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.owner_username = str(payload.get("owner_username") or row.owner_username)
            row.tenant_id = str(payload.get("tenant_id") or row.tenant_id)
            row.owner_role = str(payload.get("owner_role") or row.owner_role)
            row.updated_at = now
    return read_owner(normalized)


def _next_event_seq(session: Any, storage_job_id: str, stream: str) -> int:
    statement = (
        pg_insert(EventStreamCounter)
        .values(job_id=storage_job_id, stream=stream, last_seq=1)
        .on_conflict_do_update(
            index_elements=[EventStreamCounter.job_id, EventStreamCounter.stream],
            set_={
                "last_seq": EventStreamCounter.last_seq + 1,
                "updated_at": func.now(),
            },
        )
        .returning(EventStreamCounter.last_seq)
    )
    return int(session.execute(statement).scalar_one())


def append_event(
    job_id: str | None,
    stream: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> int | None:
    storage_job_id = _storage_job_id(job_id)
    normalized_key = str(idempotency_key or "").strip() or None
    try:
        with session_scope() as session:
            seq = _next_event_seq(session, storage_job_id, stream)
            session.add(
                JobEvent(
                    job_id=storage_job_id,
                    stream=stream,
                    seq=seq,
                    payload=payload,
                    idempotency_key=normalized_key,
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.flush()
        return seq
    except IntegrityError:
        if normalized_key is not None:
            return None
        raise


def read_events(job_id: str | None, stream: str) -> list[dict[str, Any]]:
    storage_job_id = _storage_job_id(job_id)
    with session_scope() as session:
        rows = session.execute(
            select(JobEvent)
            .where(JobEvent.job_id == storage_job_id, JobEvent.stream == stream)
            .order_by(JobEvent.seq.asc())
        ).scalars().all()
    return [dict(row.payload) if isinstance(row.payload, dict) else {} for row in rows]


def clear_events(job_id: str | None, stream: str) -> None:
    storage_job_id = _storage_job_id(job_id)
    with session_scope() as session:
        session.execute(
            delete(JobEvent).where(
                JobEvent.job_id == storage_job_id,
                JobEvent.stream == stream,
            )
        )
        session.execute(
            delete(EventStreamCounter).where(
                EventStreamCounter.job_id == storage_job_id,
                EventStreamCounter.stream == stream,
            )
        )


def replace_events(job_id: str | None, stream: str, payloads: list[dict[str, Any]]) -> None:
    storage_job_id = _storage_job_id(job_id)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        session.execute(
            delete(JobEvent).where(
                JobEvent.job_id == storage_job_id,
                JobEvent.stream == stream,
            )
        )
        session.execute(
            delete(EventStreamCounter).where(
                EventStreamCounter.job_id == storage_job_id,
                EventStreamCounter.stream == stream,
            )
        )
        for seq, payload in enumerate(payloads, start=1):
            session.add(
                JobEvent(
                    job_id=storage_job_id,
                    stream=stream,
                    seq=seq,
                    payload=payload,
                    created_at=now,
                )
            )
        if payloads:
            session.add(
                EventStreamCounter(
                    job_id=storage_job_id,
                    stream=stream,
                    last_seq=len(payloads),
                    updated_at=now,
                )
            )


def read_sent_mail_log(job_id: str | None) -> list[dict[str, Any]]:
    return read_events(job_id, "sent_mail_log")


def write_sent_mail_log_jsonl(job_id: str | None, path: Path) -> Path:
    items = read_sent_mail_log(job_id)
    if not items:
        raise FileNotFoundError("sent mail log is empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return path


def iter_sent_mail_items() -> list[tuple[str, dict[str, Any]]]:
    with session_scope() as session:
        rows = session.execute(
            select(JobEvent)
            .where(JobEvent.stream == "sent_mail_log")
            .order_by(JobEvent.job_id.asc(), JobEvent.seq.asc())
        ).scalars().all()
    result: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        if isinstance(row.payload, dict):
            result.append((row.job_id, dict(row.payload)))
    return result


def list_job_ids_with_events(stream: str) -> list[str]:
    """Return storage job ids that have at least one event in the given stream.

    Ordered by most recent activity first. Cheap single grouped query — used by
    the statistics dashboard instead of scanning the jobs directory on disk.
    """

    with session_scope() as session:
        rows = session.execute(
            select(JobEvent.job_id, func.max(JobEvent.created_at).label("last_at"))
            .where(JobEvent.stream == stream)
            .group_by(JobEvent.job_id)
            .order_by(func.max(JobEvent.created_at).desc())
        ).all()
    job_ids: list[str] = []
    for row in rows:
        job_id = str(row[0] or "").strip()
        if job_id and job_id != LEGACY_JOB_ID:
            job_ids.append(job_id)
    return job_ids


def list_job_ids_with_sent_mail() -> list[str]:
    return list_job_ids_with_events("sent_mail_log")
