"""User profile preferences."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.infra.db import session_scope
from src.infra.models import UserProfile


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_profile(username: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(UserProfile, username)
        if row is None:
            row = UserProfile(username=username)
            session.add(row)
            session.flush()
        return {
            "username": row.username,
            "display_name": row.display_name,
            "email": row.email,
            "company": row.company,
            "job_title": row.job_title,
            "signature": row.signature,
            "timezone": row.timezone,
            "mailing_defaults": dict(row.mailing_defaults or {}),
            "notifications": dict(row.notifications or {}),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def update_profile(username: str, data: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(UserProfile, username)
        if row is None:
            row = UserProfile(username=username)
            session.add(row)
        for field in ("display_name", "email", "company", "job_title", "signature", "timezone"):
            if field in data and data[field] is not None:
                setattr(row, field, str(data[field]))
        if "mailing_defaults" in data and isinstance(data["mailing_defaults"], dict):
            merged = dict(row.mailing_defaults or {})
            merged.update(data["mailing_defaults"])
            row.mailing_defaults = merged
        if "notifications" in data and isinstance(data["notifications"], dict):
            merged = dict(row.notifications or {})
            merged.update(data["notifications"])
            row.notifications = merged
        row.updated_at = _now()
        session.flush()
    return get_or_create_profile(username)
