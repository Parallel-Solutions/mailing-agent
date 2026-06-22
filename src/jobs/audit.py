from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.jobs.storage import resolve_job_paths
from src.security.auth import coerce_principal, system_principal


_AUDIT_LOCKS: dict[str, threading.Lock] = {}
_AUDIT_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _AUDIT_LOCKS_GUARD:
        lock = _AUDIT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _AUDIT_LOCKS[key] = lock
        return lock


def _audit_log_path(job_id: str | None) -> Path:
    job_paths = resolve_job_paths(job_id)
    if job_paths.uses_legacy_layout:
        return job_paths.root_dir / "state" / "audit.jsonl"
    return job_paths.root_dir / "state" / "audit.jsonl"


def append_audit_event(
    *,
    action: str,
    principal: Any = None,
    job_id: str | None = None,
    status: str = "ok",
    details: dict[str, Any] | None = None,
    audit_log_path: Path | None = None,
) -> bool:
    actor = coerce_principal(principal) if principal is not None else system_principal()
    record = {
        "event_id": uuid.uuid4().hex,
        "occurred_at": _now(),
        "action": str(action or "").strip(),
        "status": str(status or "").strip() or "ok",
        "job_id": str(job_id or "").strip(),
        "actor": {
            "username": actor.username,
            "tenant_id": actor.tenant_id,
            "role": actor.role,
        },
        "details": details or {},
    }
    path = audit_log_path or _audit_log_path(job_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with _path_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        return True
    except OSError:
        return False