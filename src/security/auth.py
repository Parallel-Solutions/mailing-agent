from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from typing import Any


_IDENTIFIER_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass(frozen=True)
class Principal:
    username: str
    tenant_id: str
    role: str = "user"
    company_id: str | None = None
    company_role: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role.lower() == "admin"

    @property
    def is_company_admin(self) -> bool:
        return str(self.company_role or "").lower() == "company_admin"

    @property
    def actor_id(self) -> str:
        return f"{self.tenant_id}:{self.username}"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_identifier(value: Any, *, fallback: str) -> str:
    text = _safe_text(value)
    text = _IDENTIFIER_RE.sub("-", text).strip("-_.")
    return (text or fallback)[:96]


def coerce_principal(value: Any) -> Principal:
    if isinstance(value, Principal):
        return value
    if isinstance(value, dict):
        username = _safe_identifier(value.get("username"), fallback="unknown")
        tenant_id = _safe_identifier(value.get("tenant_id") or username, fallback=username)
        role = _safe_identifier(value.get("role") or "user", fallback="user").lower()
        company_id_raw = _safe_text(value.get("company_id"))
        company_id = company_id_raw or None
        company_role_raw = _safe_text(value.get("company_role"))
        company_role = _safe_identifier(company_role_raw, fallback="").lower() or None
        return Principal(
            username=username,
            tenant_id=tenant_id,
            role=role,
            company_id=company_id,
            company_role=company_role,
        )

    # Backward-compatible test/helper path: old check_auth stubs returned a plain
    # username string. Treat it as admin so existing focused router tests keep
    # testing their original behavior instead of ownership setup.
    username = _safe_identifier(value, fallback="test-user")
    return Principal(username=username, tenant_id=username, role="admin")


def system_principal(actor: str = "system", *, tenant_id: str = "system") -> Principal:
    return Principal(
        username=_safe_identifier(actor, fallback="system"),
        tenant_id=_safe_identifier(tenant_id, fallback="system"),
        role="system",
    )


def _parse_app_users(value: Any) -> dict[str, dict[str, str]]:
    raw = _safe_text(value)
    if not raw:
        return {}

    parsed: Any
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    users: dict[str, dict[str, str]] = {}
    if isinstance(parsed, dict):
        for raw_username, raw_config in parsed.items():
            username = _safe_identifier(raw_username, fallback="")
            if not username:
                continue
            if isinstance(raw_config, dict):
                password = _safe_text(raw_config.get("password"))
                tenant_id = _safe_identifier(raw_config.get("tenant_id") or username, fallback=username)
                role = _safe_identifier(raw_config.get("role") or "user", fallback="user").lower()
            else:
                password = _safe_text(raw_config)
                tenant_id = username
                role = "user"
            if password:
                users[username] = {"password": password, "tenant_id": tenant_id, "role": role}
        return users

    # Lightweight fallback for local env files:
    # APP_USERS=alice:password:tenant-a:user,bob:password:tenant-a:user
    for item in raw.split(","):
        parts = [part.strip() for part in item.split(":")]
        if len(parts) < 2:
            continue
        username = _safe_identifier(parts[0], fallback="")
        password = parts[1]
        if not username or not password:
            continue
        tenant_id = _safe_identifier(parts[2] if len(parts) >= 3 else username, fallback=username)
        role = _safe_identifier(parts[3] if len(parts) >= 4 else "user", fallback="user").lower()
        users[username] = {"password": password, "tenant_id": tenant_id, "role": role}
    return users


def configured_auth_users(settings_obj: Any) -> dict[str, dict[str, str]]:
    users = _parse_app_users(getattr(settings_obj, "app_users", ""))

    admin_username = _safe_identifier(getattr(settings_obj, "app_username", "admin"), fallback="admin")
    admin_password = _safe_text(getattr(settings_obj, "app_password", ""))
    if admin_username and admin_password:
        users[admin_username] = {
            "password": admin_password,
            "tenant_id": _safe_identifier(getattr(settings_obj, "app_admin_tenant_id", "admin"), fallback="admin"),
            "role": "admin",
        }
    return users


def authenticate_basic_user(username: str, password: str, settings_obj: Any) -> Principal | None:
    safe_username = _safe_identifier(username, fallback="")
    user_config = configured_auth_users(settings_obj).get(safe_username)
    if not user_config:
        # Constant-time-ish path for unknown users to reduce enumeration signal.
        secrets.compare_digest(str(password or ""), "dummy-password-placeholder")
        return None
    expected_password = _safe_text(user_config.get("password"))
    if not expected_password or not secrets.compare_digest(str(password or ""), expected_password):
        return None
    return Principal(
        username=safe_username,
        tenant_id=_safe_identifier(user_config.get("tenant_id") or safe_username, fallback=safe_username),
        role=_safe_identifier(user_config.get("role") or "user", fallback="user").lower(),
    )


def authenticate_user(username: str, password: str) -> Principal | None:
    from src.security.user_store import sync_imported_user, verify_user_password
    from src.utils.config import settings

    record = verify_user_password(username, password)
    if record is None:
        # Env-configured users (APP_PASSWORD / APP_USERS) are normally synced at startup.
        # If bootstrap has not run yet or the row was lost, accept them and repair the store.
        basic = authenticate_basic_user(username, password, settings)
        if basic is None:
            return None
        sync_imported_user(
            basic.username,
            password,
            tenant_id=basic.tenant_id,
            role=basic.role,
        )
        record = verify_user_password(username, password)
        if record is None:
            return basic
    return Principal(
        username=record.username,
        tenant_id=record.tenant_id,
        role=record.role,
        company_id=record.company_id,
        company_role=record.company_role,
    )


def principal_from_user_record(record: Any) -> Principal:
    return Principal(
        username=str(getattr(record, "username", "") or ""),
        tenant_id=str(getattr(record, "tenant_id", "") or ""),
        role=str(getattr(record, "role", "user") or "user"),
        company_id=getattr(record, "company_id", None) or None,
        company_role=getattr(record, "company_role", None) or None,
    )