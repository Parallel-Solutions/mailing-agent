from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.jobs.job_docs import read_owner, write_owner
from src.jobs.storage import normalize_job_id, resolve_job_paths
from src.security.auth import Principal, coerce_principal


OWNER_FILENAME = "owner.json"


class JobAccessDenied(PermissionError):
    def __init__(self, message: str, *, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def job_owner_path(job_id: str | None) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / OWNER_FILENAME


def read_job_owner(job_id: str | None) -> dict[str, Any]:
    if not normalize_job_id(job_id):
        return {}
    return read_owner(job_id)


def assign_job_owner(job_id: str | None, principal: Any, *, overwrite: bool = False) -> dict[str, Any]:
    normalized_job_id = normalize_job_id(job_id)
    if not normalized_job_id:
        return {}
    actor = coerce_principal(principal)
    existing = read_job_owner(normalized_job_id)
    if existing and not overwrite:
        return existing

    now = _now()
    payload = {
        "job_id": normalized_job_id,
        "owner_username": actor.username,
        "tenant_id": actor.tenant_id,
        "owner_role": actor.role,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }
    write_owner(normalized_job_id, payload)
    return payload


def _same_owner(owner: dict[str, Any], principal: Principal) -> bool:
    owner_username = str(owner.get("owner_username") or "").strip()
    return bool(owner_username and owner_username == principal.username)


def authorize_job_access(job_id: str | None, principal: Any, *, allow_missing: bool = False) -> str | None:
    actor = coerce_principal(principal)
    normalized_job_id = normalize_job_id(job_id)
    if not normalized_job_id:
        if actor.is_admin:
            return None
        raise JobAccessDenied("Доступ к legacy workspace доступен только администратору.", status_code=403)

    paths = resolve_job_paths(normalized_job_id)
    owner = read_job_owner(normalized_job_id)
    if owner:
        from src.security.company_access import can_view_owned_resource

        if can_view_owned_resource(actor, str(owner.get("owner_username") or "")):
            return normalized_job_id
        raise JobAccessDenied("Нет доступа к этому job.", status_code=403)

    if actor.is_admin:
        return normalized_job_id

    if not paths.root_dir.exists():
        if allow_missing:
            return normalized_job_id
        raise JobAccessDenied("Job не найден.", status_code=404)
    raise JobAccessDenied("Job не найден или не назначен текущему пользователю.", status_code=404)


def job_is_visible(job_id: str | None, principal: Any) -> bool:
    try:
        authorize_job_access(job_id, principal, allow_missing=False)
        return True
    except JobAccessDenied:
        return False


def require_admin(principal: Any) -> Principal:
    actor = coerce_principal(principal)
    if not actor.is_admin:
        raise JobAccessDenied("Действие доступно только администратору.", status_code=403)
    return actor


def owner_public_payload(owner: dict[str, Any]) -> dict[str, Any]:
    if not owner:
        return {}
    return {
        "owner_username": str(owner.get("owner_username") or ""),
        "tenant_id": str(owner.get("tenant_id") or ""),
        "created_at": str(owner.get("created_at") or ""),
    }


def principal_payload(principal: Any) -> dict[str, str | None]:
    actor = coerce_principal(principal)
    payload: dict[str, str | None] = {
        "username": actor.username,
        "tenant_id": actor.tenant_id,
        "role": actor.role,
        "company_id": actor.company_id,
        "company_role": actor.company_role,
    }
    return payload