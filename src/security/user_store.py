from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.security.auth import _safe_identifier
from src.security.passwords import hash_password, verify_password


_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
_MIN_PASSWORD_LENGTH = 8

_DB_LOCK = threading.Lock()
_DB_PATH: Path | None = None


class UserStoreError(ValueError):
    pass


@dataclass(frozen=True)
class UserRecord:
    username: str
    tenant_id: str
    role: str
    created_at: str


def configure_auth_db(path: Path) -> None:
    global _DB_PATH
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    _DB_PATH = resolved
    _init_schema()


def auth_db_path() -> Path:
    if _DB_PATH is None:
        raise RuntimeError("Auth database is not configured.")
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    path = auth_db_path()
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _init_schema() -> None:
    with _DB_LOCK:
        with _connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_username ON sessions(username);
                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
                """
            )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
    with _DB_LOCK:
        with _connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM users WHERE username = ? LIMIT 1",
                (safe_username,),
            ).fetchone()
    return row is not None


def get_user_record(username: str) -> UserRecord | None:
    safe_username = _safe_identifier(username, fallback="")
    if not safe_username:
        return None
    with _DB_LOCK:
        with _connect() as connection:
            row = connection.execute(
                "SELECT username, tenant_id, role, created_at FROM users WHERE username = ?",
                (safe_username,),
            ).fetchone()
    if row is None:
        return None
    return UserRecord(
        username=str(row["username"]),
        tenant_id=str(row["tenant_id"]),
        role=str(row["role"]),
        created_at=str(row["created_at"]),
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

    with _DB_LOCK:
        with _connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM users WHERE username = ? LIMIT 1",
                (safe_username,),
            ).fetchone()
            if existing is not None:
                raise UserStoreError("Пользователь с таким логином уже существует.")
            connection.execute(
                """
                INSERT INTO users (username, password_hash, tenant_id, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (safe_username, hash_password(safe_password), safe_tenant, safe_role, created_at),
            )
    return UserRecord(
        username=safe_username,
        tenant_id=safe_tenant,
        role=safe_role,
        created_at=created_at,
    )


def verify_user_password(username: str, password: str) -> UserRecord | None:
    safe_username = _safe_identifier(username, fallback="")
    if not safe_username:
        return None
    with _DB_LOCK:
        with _connect() as connection:
            row = connection.execute(
                "SELECT username, tenant_id, role, created_at, password_hash FROM users WHERE username = ?",
                (safe_username,),
            ).fetchone()
    if row is None:
        return None
    if not verify_password(password, str(row["password_hash"])):
        return None
    return UserRecord(
        username=str(row["username"]),
        tenant_id=str(row["tenant_id"]),
        role=str(row["role"]),
        created_at=str(row["created_at"]),
    )


def has_admin_user() -> bool:
    with _DB_LOCK:
        with _connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1",
            ).fetchone()
    return row is not None


def list_usernames() -> set[str]:
    with _DB_LOCK:
        with _connect() as connection:
            rows = connection.execute("SELECT username FROM users").fetchall()
    return {str(row["username"]) for row in rows}


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


def user_record_to_dict(record: UserRecord) -> dict[str, Any]:
    return {
        "username": record.username,
        "tenant_id": record.tenant_id,
        "role": record.role,
        "created_at": record.created_at,
    }
