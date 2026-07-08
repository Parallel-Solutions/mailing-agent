from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from src.jobs.job_docs import append_event
from src.security.auth import coerce_principal, system_principal


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def append_audit_event(
    *,
    action: str,
    principal: Any = None,
    job_id: str | None = None,
    status: str = "ok",
    details: dict[str, Any] | None = None,
    audit_log_path=None,
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
    try:
        append_event(job_id, "audit", record)
        return True
    except Exception:
        return False
