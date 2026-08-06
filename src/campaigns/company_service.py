"""Company CRUD, logo storage, and membership management."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from src.infra.db import session_scope
from src.infra.models import Company, CompanyAccessGrant, CompanyMembership, User
from src.infra.object_store import ObjectNotFoundError, delete as delete_object, get_bytes, put_bytes
from src.security.auth import _safe_identifier
from src.security.company_access import (
    COMPANY_ADMIN_ROLE,
    COMPANY_MEMBER_ROLE,
    COMPANY_ROLES,
    invalidate_company_members_cache,
)
from src.security.user_store import UserStoreError, create_user, username_exists, validate_username
from src.utils.logger import logger

LOGO_MAX_BYTES = 2 * 1024 * 1024
LOGO_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


class CompanyServiceError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _company_to_dict(row: Company, *, member_count: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "phone": row.phone,
        "contact_person_name": row.contact_person_name,
        "logo_url": f"/api/v1/companies/{row.id}/logo" if row.logo_storage_key else None,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
        "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else None,
    }
    if member_count is not None:
        payload["member_count"] = member_count
    return payload


def _membership_to_dict(row: CompanyMembership) -> dict[str, Any]:
    return {
        "company_id": row.company_id,
        "username": row.username,
        "role": row.role,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
    }


def _normalize_company_role(role: str | None) -> str:
    safe = _safe_identifier(role or COMPANY_MEMBER_ROLE, fallback=COMPANY_MEMBER_ROLE).lower()
    if safe not in COMPANY_ROLES:
        raise CompanyServiceError("Роль компании должна быть company_admin или member.")
    return safe


def list_companies(
    *,
    limit: int = 100,
    offset: int = 0,
    company_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    if company_ids is not None and not company_ids:
        return {"items": [], "total": 0}
    with session_scope() as session:
        stmt = select(Company).order_by(Company.name.asc())
        if company_ids is not None:
            stmt = stmt.where(Company.id.in_(company_ids))
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(stmt.limit(limit).offset(offset)).all()
        items = []
        for row in rows:
            count = session.scalar(
                select(func.count()).select_from(CompanyMembership).where(
                    CompanyMembership.company_id == row.id
                )
            )
            items.append(_company_to_dict(row, member_count=int(count or 0)))
        return {"items": items, "total": int(total)}


def get_company(company_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            return None
        count = session.scalar(
            select(func.count()).select_from(CompanyMembership).where(CompanyMembership.company_id == row.id)
        )
        return _company_to_dict(row, member_count=int(count or 0))


def get_company_for_user(username: str) -> dict[str, Any] | None:
    with session_scope() as session:
        membership = session.execute(
            select(CompanyMembership.company_id).where(CompanyMembership.username == username)
        ).scalar_one_or_none()
        if not membership:
            return None
        row = session.get(Company, membership)
        if row is None:
            return None
        count = session.scalar(
            select(func.count()).select_from(CompanyMembership).where(CompanyMembership.company_id == row.id)
        )
        return _company_to_dict(row, member_count=int(count or 0))


def create_company(
    *,
    name: str,
    phone: str = "",
    contact_person_name: str = "",
    managed_by_username: str | None = None,
) -> dict[str, Any]:
    safe_name = str(name or "").strip()
    if not safe_name:
        raise CompanyServiceError("Название компании обязательно.")
    company_id = str(uuid.uuid4())
    now = _now()
    with session_scope() as session:
        row = Company(
            id=company_id,
            name=safe_name[:255],
            phone=str(phone or "").strip()[:64],
            contact_person_name=str(contact_person_name or "").strip()[:255],
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        if managed_by_username:
            manager = session.get(User, managed_by_username)
            if manager is None:
                raise CompanyServiceError("Администратор компании не найден.")
            session.add(
                CompanyAccessGrant(
                    company_id=company_id,
                    username=manager.username,
                    access_level="manage",
                    created_by=manager.username,
                )
            )
        session.flush()
        return _company_to_dict(row, member_count=0)


def update_company(company_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            return None
        if "name" in data and data["name"] is not None:
            safe_name = str(data["name"]).strip()
            if not safe_name:
                raise CompanyServiceError("Название компании обязательно.")
            row.name = safe_name[:255]
        if "phone" in data and data["phone"] is not None:
            row.phone = str(data["phone"]).strip()[:64]
        if "contact_person_name" in data and data["contact_person_name"] is not None:
            row.contact_person_name = str(data["contact_person_name"]).strip()[:255]
        row.updated_at = _now()
        session.flush()
        count = session.scalar(
            select(func.count()).select_from(CompanyMembership).where(CompanyMembership.company_id == row.id)
        )
        return _company_to_dict(row, member_count=int(count or 0))


def delete_company(company_id: str) -> bool:
    logo_storage_key: str | None = None
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            return False
        manager_usernames = list(
            session.scalars(
                select(CompanyAccessGrant.username).where(
                    CompanyAccessGrant.company_id == company_id,
                    CompanyAccessGrant.access_level == "manage",
                )
            ).all()
        )
        logo_storage_key = row.logo_storage_key
        session.delete(row)
        session.flush()
        for username in manager_usernames:
            user = session.get(User, username)
            if user is None or user.role != "company_admin":
                continue
            remaining_manage = session.scalar(
                select(CompanyAccessGrant.company_id)
                .where(
                    CompanyAccessGrant.username == username,
                    CompanyAccessGrant.access_level == "manage",
                )
                .limit(1)
            )
            if remaining_manage is None:
                user.role = "user"

    invalidate_company_members_cache(company_id)
    if logo_storage_key:
        try:
            delete_object(logo_storage_key)
        except Exception:
            logger.warning(
                "company_logo_cleanup_failed",
                company_id=company_id,
                storage_key=logo_storage_key,
                exc_info=True,
            )
    return True


def upload_company_logo(company_id: str, data: bytes, content_type: str) -> dict[str, Any] | None:
    if len(data) > LOGO_MAX_BYTES:
        raise CompanyServiceError("Логотип не должен превышать 2 МБ.")
    ext = LOGO_CONTENT_TYPES.get(str(content_type or "").lower())
    if not ext:
        raise CompanyServiceError("Поддерживаются только PNG, JPEG и WebP.")
    storage_key = f"companies/{company_id}/logo.{ext}"
    put_bytes(storage_key, data, content_type=content_type)
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            return None
        row.logo_storage_key = storage_key
        row.updated_at = _now()
        session.flush()
        count = session.scalar(
            select(func.count()).select_from(CompanyMembership).where(CompanyMembership.company_id == row.id)
        )
        return _company_to_dict(row, member_count=int(count or 0))


def delete_company_logo(company_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            return None
        if row.logo_storage_key:
            try:
                delete_object(row.logo_storage_key)
            except ObjectNotFoundError:
                pass
            row.logo_storage_key = None
            row.updated_at = _now()
            session.flush()
        count = session.scalar(
            select(func.count()).select_from(CompanyMembership).where(CompanyMembership.company_id == row.id)
        )
        return _company_to_dict(row, member_count=int(count or 0))


def get_company_logo(company_id: str) -> tuple[bytes, str] | None:
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None or not row.logo_storage_key:
            return None
        key = row.logo_storage_key
    ext = key.rsplit(".", 1)[-1].lower()
    content_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")
    try:
        data = get_bytes(key)
    except ObjectNotFoundError:
        return None
    return data, content_type


def list_members(company_id: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(CompanyMembership)
            .where(CompanyMembership.company_id == company_id)
            .order_by(CompanyMembership.created_at.asc())
        ).all()
        return [_membership_to_dict(row) for row in rows]


def _user_has_membership(username: str, session) -> bool:
    existing = session.execute(
        select(CompanyMembership.company_id).where(CompanyMembership.username == username)
    ).scalar_one_or_none()
    return existing is not None


def _sync_membership_admin_access(
    session,
    *,
    company_id: str,
    username: str,
    role: str,
) -> None:
    grant = session.get(
        CompanyAccessGrant,
        {"company_id": company_id, "username": username},
    )
    user = session.get(User, username)
    if role == COMPANY_ADMIN_ROLE:
        if grant is None:
            session.add(
                CompanyAccessGrant(
                    company_id=company_id,
                    username=username,
                    access_level="manage",
                    created_by="membership",
                )
            )
        elif grant.access_level != "manage":
            grant.access_level = "manage"
            grant.created_by = "membership"
        if user is not None and user.role != "admin":
            user.role = "company_admin"
        return

    if grant is not None and grant.created_by in {"membership", "migration"}:
        session.delete(grant)

    if user is not None and user.role == "company_admin":
        remaining_manage = session.scalar(
            select(CompanyAccessGrant.company_id)
            .where(
                CompanyAccessGrant.username == username,
                CompanyAccessGrant.access_level == "manage",
            )
            .limit(1)
        )
        if remaining_manage is None:
            user.role = "user"


def add_member(
    company_id: str,
    username: str,
    *,
    role: str = COMPANY_MEMBER_ROLE,
    password: str | None = None,
) -> dict[str, Any]:
    safe_username = validate_username(username)
    safe_role = _normalize_company_role(role)
    if not username_exists(safe_username):
        if not password:
            raise CompanyServiceError("Пользователь не найден. Укажите пароль для создания аккаунта.")
        create_user(safe_username, password, tenant_id=company_id, role="user")
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise CompanyServiceError("Компания не найдена.")
        if _user_has_membership(safe_username, session):
            raise CompanyServiceError("Пользователь уже состоит в компании.")
        row = CompanyMembership(
            company_id=company_id,
            username=safe_username,
            role=safe_role,
            created_at=_now(),
        )
        session.add(row)
        _sync_membership_admin_access(
            session,
            company_id=company_id,
            username=safe_username,
            role=safe_role,
        )
        session.flush()
        invalidate_company_members_cache(company_id)
        return _membership_to_dict(row)


def update_member_role(company_id: str, username: str, role: str) -> dict[str, Any] | None:
    safe_username = validate_username(username)
    safe_role = _normalize_company_role(role)
    with session_scope() as session:
        row = session.get(CompanyMembership, {"company_id": company_id, "username": safe_username})
        if row is None:
            return None
        row.role = safe_role
        _sync_membership_admin_access(
            session,
            company_id=company_id,
            username=safe_username,
            role=safe_role,
        )
        session.flush()
        invalidate_company_members_cache(company_id)
        return _membership_to_dict(row)


def remove_member(company_id: str, username: str) -> bool:
    safe_username = validate_username(username)
    with session_scope() as session:
        row = session.get(CompanyMembership, {"company_id": company_id, "username": safe_username})
        if row is None:
            return False
        _sync_membership_admin_access(
            session,
            company_id=company_id,
            username=safe_username,
            role=COMPANY_MEMBER_ROLE,
        )
        session.delete(row)
        session.flush()
        invalidate_company_members_cache(company_id)
        return True


def create_user_in_company(
    company_id: str,
    username: str,
    password: str,
    *,
    company_role: str = COMPANY_MEMBER_ROLE,
) -> dict[str, Any]:
    try:
        create_user(username, password, tenant_id=company_id, role="user")
    except UserStoreError as exc:
        raise CompanyServiceError(str(exc)) from exc
    return add_member(company_id, username, role=company_role, password=None)


def _normalise_work_types(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    for value in raw:
        if not isinstance(value, dict):
            continue
        work_type_id = str(value.get("id") or "").strip()
        name = str(value.get("name") or "").strip()
        if not work_type_id or not name:
            continue
        items.append({"id": work_type_id, "name": name})
    return items


def _validate_work_type_name(name: str, *, existing: list[dict[str, str]], exclude_id: str | None = None) -> str:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise CompanyServiceError("Укажите название вида работ")
    if len(clean_name) > 128:
        raise CompanyServiceError("Название вида работ не должно превышать 128 символов")
    clean_fold = clean_name.casefold()
    for item in existing:
        if exclude_id and item["id"] == exclude_id:
            continue
        if item["name"].casefold() == clean_fold:
            raise CompanyServiceError("Вид работ с таким названием уже существует")
    return clean_name


def list_company_work_types(company_id: str) -> list[dict[str, str]]:
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            raise CompanyServiceError("Компания не найдена.")
        return _normalise_work_types(row.work_types)


def create_company_work_type(company_id: str, *, name: str) -> dict[str, str]:
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            raise CompanyServiceError("Компания не найдена.")
        items = _normalise_work_types(row.work_types)
        clean_name = _validate_work_type_name(name, existing=items)
        item = {"id": str(uuid.uuid4()), "name": clean_name}
        items.append(item)
        row.work_types = items
        row.updated_at = _now()
        session.flush()
        return item


def update_company_work_type(company_id: str, work_type_id: str, *, name: str) -> dict[str, str] | None:
    safe_id = str(work_type_id or "").strip()
    if not safe_id:
        return None
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            return None
        items = _normalise_work_types(row.work_types)
        clean_name = _validate_work_type_name(name, existing=items, exclude_id=safe_id)
        updated: dict[str, str] | None = None
        next_items: list[dict[str, str]] = []
        for item in items:
            if item["id"] == safe_id:
                updated = {"id": safe_id, "name": clean_name}
                next_items.append(updated)
            else:
                next_items.append(item)
        if updated is None:
            return None
        row.work_types = next_items
        row.updated_at = _now()
        session.flush()
        return updated


def delete_company_work_type(company_id: str, work_type_id: str) -> bool:
    safe_id = str(work_type_id or "").strip()
    if not safe_id:
        return False
    with session_scope() as session:
        row = session.get(Company, company_id)
        if row is None:
            return False
        items = _normalise_work_types(row.work_types)
        next_items = [item for item in items if item["id"] != safe_id]
        if len(next_items) == len(items):
            return False
        row.work_types = next_items
        row.updated_at = _now()
        session.flush()
        return True
