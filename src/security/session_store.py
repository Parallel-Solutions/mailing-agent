from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

from src.security.user_store import _DB_LOCK, _connect, auth_db_path, configure_auth_db


SESSION_COOKIE_NAME = "mailing_agent_session"


def _now() -> datetime:
    return datetime.now()


def _expires_at(ttl_days: int) -> str:
    return (_now() + timedelta(days=max(1, ttl_days))).isoformat(timespec="seconds")


def create_session(username: str, *, ttl_days: int = 7) -> str:
    token = secrets.token_urlsafe(32)
    created_at = _now().isoformat(timespec="seconds")
    expires = _expires_at(ttl_days)
    with _DB_LOCK:
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (token, username, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (token, username, expires, created_at),
            )
    return token


def delete_session(token: str) -> None:
    if not token:
        return
    with _DB_LOCK:
        with _connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token = ?", (token,))


def get_session_username(token: str, *, ttl_days: int = 7) -> str | None:
    raw_token = str(token or "").strip()
    if not raw_token:
        return None
    cleanup_expired_sessions()
    with _DB_LOCK:
        with _connect() as connection:
            row = connection.execute(
                "SELECT username, expires_at FROM sessions WHERE token = ?",
                (raw_token,),
            ).fetchone()
    if row is None:
        return None
    expires_text = str(row["expires_at"] or "")
    try:
        expires_at = datetime.fromisoformat(expires_text)
    except ValueError:
        delete_session(raw_token)
        return None
    if expires_at <= _now():
        delete_session(raw_token)
        return None
    return str(row["username"])


def cleanup_expired_sessions() -> None:
    now_text = _now().isoformat(timespec="seconds")
    with _DB_LOCK:
        with _connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_text,))


def init_session_store(db_path) -> None:
    configure_auth_db(db_path)
