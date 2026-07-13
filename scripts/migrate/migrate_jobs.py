from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from src.infra.db import init_db, session_scope
from src.infra.models import AgentState, JobDoc, JobEvent, JobOwner
from src.infra.object_store import exists as s3_exists
from src.infra.object_store import job_key, put_file
from src.jobs.clients_store import import_clients_from_xlsx
from src.jobs.job_docs import append_event
from scripts.migrate.legacy_paths import iter_jobs_dirs
from src.jobs.storage import DATA_DIR, normalize_job_id


def _read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _upsert_agent_state(job_id: str, agent_name: str, state_path: Path, details_path: Path | None = None) -> None:
    data = _read_json_file(state_path)
    if not data:
        return
    details = _read_json_file(details_path) if details_path and details_path.exists() else None
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(AgentState, {"job_id": job_id, "agent_name": agent_name})
        if row is None:
            session.add(
                AgentState(
                    job_id=job_id,
                    agent_name=agent_name,
                    state=data,
                    details=details,
                    updated_at=now,
                )
            )
        else:
            row.state = data
            row.details = details
            row.updated_at = now


def _stream_has_events(job_id: str, stream: str) -> bool:
    with session_scope() as session:
        count = session.execute(
            select(func.count())
            .select_from(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.stream == stream)
        ).scalar_one()
    return int(count or 0) > 0


def _migrate_job_dir(job_dir: Path, *, legacy: bool = False) -> dict[str, int]:
    counts = {"files": 0, "events": 0, "states": 0, "docs": 0}
    job_id = "__legacy__" if legacy else normalize_job_id(job_dir.name)
    if not job_id:
        return counts

    state_dir = job_dir / "state" if not legacy else DATA_DIR / "state"
    if state_dir.exists():
        owner_path = state_dir / "owner.json"
        if owner_path.exists():
            owner = _read_json_file(owner_path)
            if isinstance(owner, dict) and owner:
                now = datetime.now(timezone.utc)
                with session_scope() as session:
                    if session.get(JobOwner, job_id if not legacy else job_id) is None:
                        session.add(
                            JobOwner(
                                job_id=job_id,
                                owner_username=str(owner.get("owner_username") or ""),
                                tenant_id=str(owner.get("tenant_id") or ""),
                                owner_role=str(owner.get("owner_role") or "user"),
                                created_at=now,
                                updated_at=now,
                            )
                        )
                        counts["docs"] += 1
        for agent_file in state_dir.glob("*.json"):
            if agent_file.name.endswith(".details.json") or agent_file.name == "owner.json":
                continue
            agent_name = agent_file.stem
            details_path = state_dir / f"{agent_name}.details.json"
            _upsert_agent_state(job_id, agent_name, agent_file, details_path if details_path.exists() else None)
            counts["states"] += 1
        for jsonl_file in state_dir.glob("*.jsonl"):
            stream = jsonl_file.stem
            storage_job_id = job_id if not legacy else "__legacy__"
            if _stream_has_events(storage_job_id, stream):
                continue
            for record in _read_jsonl_file(jsonl_file):
                append_event(job_id if not legacy else None, stream, record)
                counts["events"] += 1
        for doc_name in ("consents.json", "agent_tasks.json", "agent_events.json", "load_test.json"):
            doc_path = state_dir / doc_name
            if not doc_path.exists():
                continue
            payload = _read_json_file(doc_path)
            if isinstance(payload, dict):
                with session_scope() as session:
                    if session.get(JobDoc, {"job_id": job_id, "name": doc_name[:-5]}) is None:
                        session.add(JobDoc(job_id=job_id, name=doc_name[:-5], payload=payload, updated_at=datetime.now(timezone.utc)))
                        counts["docs"] += 1

    upload_dirs = [
        ("input", job_dir / "input"),
        ("templates", job_dir / "templates"),
        ("output", job_dir / "output"),
        ("consents", job_dir / "consents"),
        ("reports", job_dir / "reports"),
        ("archives", job_dir / "archives"),
    ]
    if legacy:
        upload_dirs = [
            ("input", DATA_DIR),
            ("templates", DATA_DIR / "templates"),
            ("output", DATA_DIR / "output"),
            ("consents", DATA_DIR / "consents"),
        ]
    for prefix, local_dir in upload_dirs:
        if not local_dir.exists():
            continue
        for file_path in local_dir.rglob("*"):
            if not file_path.is_file():
                continue
            if legacy and prefix == "input" and file_path.name != "data.xlsx":
                continue
            relative = file_path.relative_to(job_dir if not legacy else DATA_DIR).as_posix()
            key = job_key(job_id, relative)
            if not s3_exists(key):
                put_file(key, file_path)
                counts["files"] += 1
            if file_path.name == "data.xlsx" and not legacy:
                import_clients_from_xlsx(job_id, file_path)

    sent_log = job_dir / "sent_mail_log.jsonl" if not legacy else DATA_DIR / "sent_mail_log.jsonl"
    storage_job_id = job_id if not legacy else "__legacy__"
    if sent_log.exists() and not _stream_has_events(storage_job_id, "sent_mail_log"):
        for record in _read_jsonl_file(sent_log):
            append_event(job_id if not legacy else None, "sent_mail_log", record)
            counts["events"] += 1
    return counts


def migrate_jobs() -> dict[str, dict[str, int]]:
    init_db()
    report: dict[str, dict[str, int]] = {}
    migrated_job_ids: set[str] = set()
    for jobs_dir in iter_jobs_dirs():
        for job_dir in sorted(path for path in jobs_dir.iterdir() if path.is_dir()):
            job_id = normalize_job_id(job_dir.name)
            if not job_id or job_id in migrated_job_ids:
                continue
            migrated_job_ids.add(job_id)
            report[job_dir.name] = _migrate_job_dir(job_dir, legacy=False)
    report["__legacy__"] = _migrate_job_dir(DATA_DIR, legacy=True)
    return report


if __name__ == "__main__":
    print(json.dumps(migrate_jobs(), ensure_ascii=False, indent=2))
