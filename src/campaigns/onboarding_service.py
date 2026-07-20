"""Persistent, per-user state for the product onboarding tour."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.infra.db import session_scope
from src.infra.models import UserOnboardingState


ONBOARDING_VERSION = 4
ONBOARDING_STEP_COUNT = 27
ONBOARDING_STATUSES = {"active", "paused", "dismissed", "completed"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(row: UserOnboardingState | None) -> dict[str, Any]:
    if row is None:
        # Accounts that existed before onboarding was introduced stay undisturbed.
        return {
            "version": ONBOARDING_VERSION,
            "status": "dismissed",
            "current_step": 0,
            "completed_steps": [],
            "step_count": ONBOARDING_STEP_COUNT,
            "available": True,
            "paused_at": None,
            "dismissed_at": None,
            "completed_at": None,
            "updated_at": None,
        }
    return {
        "version": row.version,
        "status": row.status,
        "current_step": row.current_step,
        "completed_steps": list(row.completed_steps or []),
        "step_count": ONBOARDING_STEP_COUNT,
        "available": True,
        "paused_at": row.paused_at.isoformat() if row.paused_at else None,
        "dismissed_at": row.dismissed_at.isoformat() if row.dismissed_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def create_onboarding_for_new_user(session: Session, username: str) -> None:
    if session.get(UserOnboardingState, username) is None:
        session.add(
            UserOnboardingState(
                username=username,
                version=ONBOARDING_VERSION,
                status="active",
                current_step=0,
                completed_steps=[],
            )
        )


def get_onboarding(username: str) -> dict[str, Any]:
    with session_scope() as session:
        return _serialize(session.get(UserOnboardingState, username))


def update_onboarding(username: str, data: dict[str, Any]) -> dict[str, Any]:
    status = str(data.get("status") or "active")
    if status not in ONBOARDING_STATUSES:
        raise ValueError("Unsupported onboarding status.")

    with session_scope() as session:
        row = session.get(UserOnboardingState, username)
        if row is None:
            row = UserOnboardingState(
                username=username,
                version=ONBOARDING_VERSION,
                status="dismissed",
                current_step=0,
                completed_steps=[],
            )
            session.add(row)

        row.status = status
        row.version = ONBOARDING_VERSION
        if "current_step" in data:
            row.current_step = max(0, min(int(data["current_step"]), ONBOARDING_STEP_COUNT - 1))
        if "completed_steps" in data:
            row.completed_steps = list(dict.fromkeys(str(item) for item in data["completed_steps"]))

        now = _now()
        row.updated_at = now
        row.paused_at = now if status == "paused" else None
        row.dismissed_at = now if status == "dismissed" else None
        row.completed_at = now if status == "completed" else None
        session.flush()
        result = _serialize(row)
    return result


def restart_onboarding(username: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(UserOnboardingState, username)
        if row is None:
            row = UserOnboardingState(username=username)
            session.add(row)
        row.version = ONBOARDING_VERSION
        row.status = "active"
        row.current_step = 0
        row.completed_steps = []
        row.paused_at = None
        row.dismissed_at = None
        row.completed_at = None
        row.updated_at = _now()
        session.flush()
        result = _serialize(row)
    return result
