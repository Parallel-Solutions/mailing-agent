from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select

from src.infra.db import session_scope
from src.infra.models import Company, CompanyAccessGrant, CompanyMembership, User
from src.security.auth import Principal, coerce_principal

COMPANY_ADMIN_ROLE = "company_admin"
COMPANY_MEMBER_ROLE = "member"
COMPANY_ROLES = {COMPANY_ADMIN_ROLE, COMPANY_MEMBER_ROLE}
COMPANY_ACCESS_VIEW = "view"
COMPANY_ACCESS_MANAGE = "manage"
COMPANY_ACCESS_LEVELS = {COMPANY_ACCESS_VIEW, COMPANY_ACCESS_MANAGE}
OWNER_VISIBILITY_UNSET = object()


def effective_owner_visibility(
    owner_username: str,
    visible_owners: Any = OWNER_VISIBILITY_UNSET,
) -> frozenset[str] | None:
    """Default service calls to the requested owner instead of global access."""
    if visible_owners is OWNER_VISIBILITY_UNSET:
        owner = str(owner_username or "").strip()
        return frozenset({owner}) if owner else frozenset()
    return visible_owners


def _actor(value: Any) -> Principal:
    return coerce_principal(value)


def is_company_admin(actor: Principal) -> bool:
    return (
        str(actor.role or "").lower() == COMPANY_ADMIN_ROLE
        or str(actor.company_role or "").lower() == COMPANY_ADMIN_ROLE
    )


def _grant_level(username: str, company_id: str) -> str | None:
    with session_scope() as session:
        level = session.scalar(
            select(CompanyAccessGrant.access_level).where(
                CompanyAccessGrant.username == username,
                CompanyAccessGrant.company_id == company_id,
            )
        )
    return str(level or "").lower() or None


def company_accesses_for_username(username: str) -> list[dict[str, Any]]:
    safe_username = str(username or "").strip()
    if not safe_username:
        return []
    with session_scope() as session:
        rows = session.execute(
            select(CompanyAccessGrant, Company)
            .join(Company, Company.id == CompanyAccessGrant.company_id)
            .where(CompanyAccessGrant.username == safe_username)
            .order_by(Company.name.asc())
        ).all()
        payload = {
            grant.company_id: {
                "company_id": grant.company_id,
                "company_name": company.name,
                "access_level": grant.access_level,
            }
            for grant, company in rows
        }
    return list(payload.values())


def replace_company_accesses(
    username: str,
    accesses: list[dict[str, Any]],
    *,
    created_by: str,
) -> list[dict[str, Any]]:
    safe_username = str(username or "").strip()
    normalized: dict[str, str] = {}
    for item in accesses:
        company_id = str(item.get("company_id") or "").strip()
        access_level = str(item.get("access_level") or COMPANY_ACCESS_VIEW).lower()
        if not company_id:
            raise ValueError("Для каждого права нужно указать компанию.")
        if access_level not in COMPANY_ACCESS_LEVELS:
            raise ValueError("Уровень доступа должен быть view или manage.")
        if normalized.get(company_id) == COMPANY_ACCESS_MANAGE:
            continue
        normalized[company_id] = access_level

    with session_scope() as session:
        if session.get(User, safe_username) is None:
            raise ValueError("Пользователь не найден.")
        if normalized:
            existing_ids = set(
                session.scalars(select(Company.id).where(Company.id.in_(normalized))).all()
            )
            missing = set(normalized) - existing_ids
            if missing:
                raise ValueError("Одна или несколько выбранных компаний не найдены.")
        session.execute(
            delete(CompanyAccessGrant).where(
                CompanyAccessGrant.username == safe_username
            )
        )
        for company_id, access_level in normalized.items():
            session.add(
                CompanyAccessGrant(
                    company_id=company_id,
                    username=safe_username,
                    access_level=access_level,
                    created_by=str(created_by or "")[:32],
                )
            )
        session.flush()
    return company_accesses_for_username(safe_username)


def company_directory_ids(actor: Any) -> frozenset[str] | None:
    principal = _actor(actor)
    if principal.is_admin:
        return None
    accesses = company_accesses_for_username(principal.username)
    return frozenset(str(item["company_id"]) for item in accesses)


def can_view_owned_resource(actor: Any, owner_username: str) -> bool:
    principal = _actor(actor)
    owner = str(owner_username or "").strip()
    if not owner:
        return False
    if principal.is_admin:
        return True
    if principal.username == owner:
        return True
    if is_company_admin(principal) and principal.company_id:
        return owner in company_member_usernames(principal.company_id)
    return False


def can_manage_company(actor: Any, company_id: str) -> bool:
    principal = _actor(actor)
    safe_company_id = str(company_id or "").strip()
    if not safe_company_id:
        return False
    if principal.is_admin:
        return True
    if _grant_level(principal.username, safe_company_id) == COMPANY_ACCESS_MANAGE:
        return True
    return False


def can_view_company(actor: Any, company_id: str) -> bool:
    principal = _actor(actor)
    safe_company_id = str(company_id or "").strip()
    if not safe_company_id:
        return False
    if principal.is_admin:
        return True
    if _grant_level(principal.username, safe_company_id) in COMPANY_ACCESS_LEVELS:
        return True
    if principal.company_id == safe_company_id:
        return True
    return False


@lru_cache(maxsize=256)
def company_member_usernames(company_id: str) -> frozenset[str]:
    safe_company_id = str(company_id or "").strip()
    if not safe_company_id:
        return frozenset()
    with session_scope() as session:
        rows = (
            session.execute(
                select(CompanyMembership.username).where(
                    CompanyMembership.company_id == safe_company_id
                )
            )
            .scalars()
            .all()
        )
    return frozenset(str(row) for row in rows)


def invalidate_company_members_cache(company_id: str | None = None) -> None:
    if company_id:
        company_member_usernames.cache_clear()
        return
    company_member_usernames.cache_clear()


def can_access_owner(
    visible_owners: frozenset[str] | None, owner_username: str
) -> bool:
    if visible_owners is None:
        return True
    return owner_username in visible_owners


def apply_owner_filter(stmt, owner_column, visible_owners: frozenset[str] | None):
    if visible_owners is None:
        return stmt
    if len(visible_owners) == 1:
        return stmt.where(owner_column == next(iter(visible_owners)))
    return stmt.where(owner_column.in_(visible_owners))


def visible_owner_usernames(actor: Any) -> frozenset[str] | None:
    """Return None for app admin (no filter), else allowed owner usernames."""
    principal = _actor(actor)
    if principal.is_admin:
        return None
    if is_company_admin(principal) and principal.company_id:
        return company_member_usernames(principal.company_id)
    return frozenset({principal.username})


def connection_owner_usernames(actor: Any) -> frozenset[str] | None:
    """Return owners whose delivery connections the actor may access."""
    principal = _actor(actor)
    if principal.is_admin:
        return None
    return frozenset({principal.username})


def require_app_admin(actor: Any) -> Principal:
    principal = _actor(actor)
    if not principal.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Действие доступно только администратору приложения.",
        )
    return principal


def require_company_admin(actor: Any, company_id: str) -> Principal:
    principal = _actor(actor)
    if not can_manage_company(principal, company_id):
        raise HTTPException(
            status_code=403, detail="Действие доступно только администратору компании."
        )
    return principal


def require_company_view(actor: Any, company_id: str) -> Principal:
    principal = _actor(actor)
    if not can_view_company(principal, company_id):
        raise HTTPException(status_code=403, detail="Нет доступа к этой компании.")
    return principal
