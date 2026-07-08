from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from src.infra.db import session_scope
from src.infra.models import Session as SessionModel


SESSION_COOKIE_NAME = "mailing_agent_session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expires_at(ttl_days: int) -> datetime:
    return _now() + timedelta(days=max(1, ttl_days))


def create_session(username: str, *, ttl_days: int = 7) -> str:
    token = secrets.token_urlsafe(32)
    created_at = _now()
    expires = _expires_at(ttl_days)
    with session_scope() as session:
        session.add(
            SessionModel(
                token=token,
                username=username,
                expires_at=expires,
                created_at=created_at,
            )
        )
    return token


def delete_session(token: str) -> None:
    if not token:
        return
    with session_scope() as session:
        session.execute(delete(SessionModel).where(SessionModel.token == token))


def get_session_username(token: str, *, ttl_days: int = 7) -> str | None:
    raw_token = str(token or "").strip()
    if not raw_token:
        return None
    cleanup_expired_sessions()
    with session_scope() as session:
        row = session.get(SessionModel, raw_token)
    if row is None:
        return None
    if row.expires_at <= _now():
        delete_session(raw_token)
        return None
    return row.username


def cleanup_expired_sessions() -> None:
    now = _now()
    with session_scope() as session:
        session.execute(delete(SessionModel).where(SessionModel.expires_at <= now))
