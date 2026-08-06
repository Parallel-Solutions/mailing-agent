from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import CompanyMembership
from fastapi import HTTPException

from src.security.auth import Principal, coerce_principal

COMPANY_ADMIN_ROLE = "company_admin"
COMPANY_MEMBER_ROLE = "member"
COMPANY_ROLES = {COMPANY_ADMIN_ROLE, COMPANY_MEMBER_ROLE}
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
    return str(actor.company_role or "").lower() == COMPANY_ADMIN_ROLE


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
    return is_company_admin(principal) and principal.company_id == safe_company_id


def can_view_company(actor: Any, company_id: str) -> bool:
    principal = _actor(actor)
    safe_company_id = str(company_id or "").strip()
    if not safe_company_id:
        return False
    if principal.is_admin:
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
