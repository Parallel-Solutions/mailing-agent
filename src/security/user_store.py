from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.campaigns.onboarding_service import create_onboarding_for_new_user
from src.infra.db import session_scope
from src.infra.models import User
from src.security.auth import _safe_identifier
from src.security.passwords import dummy_verify_password, hash_password, verify_password


_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
_MIN_PASSWORD_LENGTH = 8


class UserStoreError(ValueError):
    pass


@dataclass(frozen=True)
class UserRecord:
    username: str
    tenant_id: str
    role: str
    created_at: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().replace(microsecond=0).isoformat()


def validate_username(username: str) -> str:
    safe_username = _safe_identifier(username, fallback="")
    if not _USERNAME_RE.fullmatch(safe_username):
        raise UserStoreError("Логин должен содержать 3–32 символа: буквы, цифры, _, . или -.")
    return safe_username


def validate_password(password: str) -> str:
    raw = str(password or "")
    if len(raw) < _MIN_PASSWORD_LENGTH:
        raise UserStoreError(f"Пароль должен быть не короче {_MIN_PASSWORD_LENGTH} символов.")
    return raw


def username_exists(username: str) -> bool:
    safe_username = validate_username(username)
    with session_scope() as session:
        row = session.get(User, safe_username)
    return row is not None


def get_user_record(username: str) -> UserRecord | None:
    safe_username = _safe_identifier(username, fallback="")
    if not safe_username:
        return None
    with session_scope() as session:
        row = session.get(User, safe_username)
    if row is None:
        return None
    return UserRecord(
        username=row.username,
        tenant_id=row.tenant_id,
        role=row.role,
        created_at=row.created_at.isoformat(timespec="seconds"),
    )


def create_user(
    username: str,
    password: str,
    *,
    tenant_id: str | None = None,
    role: str = "user",
) -> UserRecord:
    safe_username = validate_username(username)
    safe_password = validate_password(password)
    safe_tenant = _safe_identifier(tenant_id or safe_username, fallback=safe_username)
    safe_role = _safe_identifier(role or "user", fallback="user").lower()
    created_at = _now()

    with session_scope() as session:
        existing = session.get(User, safe_username)
        if existing is not None:
            raise UserStoreError("Пользователь с таким логином уже существует.")
        session.add(
            User(
                username=safe_username,
                password_hash=hash_password(safe_password),
                tenant_id=safe_tenant,
                role=safe_role,
                created_at=created_at,
            )
        )
        session.flush()
        create_onboarding_for_new_user(session, safe_username)
    return UserRecord(
        username=safe_username,
        tenant_id=safe_tenant,
        role=safe_role,
        created_at=created_at.isoformat(timespec="seconds"),
    )


def verify_user_password(username: str, password: str) -> UserRecord | None:
    safe_username = _safe_identifier(username, fallback="")
    if not safe_username:
        dummy_verify_password()
        return None
    with session_scope() as session:
        row = session.get(User, safe_username)
    if row is None:
        dummy_verify_password()
        return None
    if not verify_password(password, row.password_hash):
        return None
    return UserRecord(
        username=row.username,
        tenant_id=row.tenant_id,
        role=row.role,
        created_at=row.created_at.isoformat(timespec="seconds"),
    )


def has_admin_user() -> bool:
    with session_scope() as session:
        row = session.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
    return row is not None


def list_usernames() -> set[str]:
    with session_scope() as session:
        rows = session.execute(select(User.username)).scalars().all()
    return set(rows)


def import_user_if_missing(
    username: str,
    password: str,
    *,
    tenant_id: str,
    role: str,
) -> UserRecord | None:
    safe_username = _safe_identifier(username, fallback="")
    if not safe_username or not str(password or "").strip():
        return None
    if username_exists(safe_username):
        return get_user_record(safe_username)
    return create_user(
        safe_username,
        password,
        tenant_id=_safe_identifier(tenant_id or safe_username, fallback=safe_username),
        role=_safe_identifier(role or "user", fallback="user").lower(),
    )


def sync_imported_user(
    username: str,
    password: str,
    *,
    tenant_id: str,
    role: str,
) -> UserRecord | None:
    safe_username = _safe_identifier(username, fallback="")
    if not safe_username or not str(password or "").strip():
        return None
    safe_username = validate_username(safe_username)
    safe_password = validate_password(password)
    safe_tenant = _safe_identifier(tenant_id or safe_username, fallback=safe_username)
    safe_role = _safe_identifier(role or "user", fallback="user").lower()
    created_at = _now()

    with session_scope() as session:
        existing = session.get(User, safe_username)
        if existing is None:
            session.add(
                User(
                    username=safe_username,
                    password_hash=hash_password(safe_password),
                    tenant_id=safe_tenant,
                    role=safe_role,
                    created_at=created_at,
                )
            )
            session.flush()
            create_onboarding_for_new_user(session, safe_username)
            created_text = created_at.isoformat(timespec="seconds")
        else:
            created_text = existing.created_at.isoformat(timespec="seconds")
            existing.password_hash = hash_password(safe_password)
            existing.tenant_id = safe_tenant
            existing.role = safe_role
    return UserRecord(
        username=safe_username,
        tenant_id=safe_tenant,
        role=safe_role,
        created_at=created_text,
    )


def user_record_to_dict(record: UserRecord) -> dict[str, Any]:
    return {
        "username": record.username,
        "tenant_id": record.tenant_id,
        "role": record.role,
        "created_at": record.created_at,
    }
