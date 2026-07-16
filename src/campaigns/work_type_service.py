"""Work type catalogue for campaign setup."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.generator.generation.work_types import (
    WORK_TYPE_MNGP_DISTRICTS,
    WORK_TYPE_MNGP_SETTLEMENTS,
    WORK_TYPE_PROFILES,
)
from src.infra.db import session_scope
from src.infra.models import UserProfile


CUSTOM_WORK_TYPES_KEY = "work_types"


def _system_name(key: str, label: str) -> str:
    if key == WORK_TYPE_MNGP_SETTLEMENTS:
        return "МНГП (поселения и городские округа)"
    if key == WORK_TYPE_MNGP_DISTRICTS:
        return "МНГП (муниципальные районы)"
    return label


def _system_items() -> list[dict[str, Any]]:
    return [
        {
            "key": profile.key,
            "name": _system_name(profile.key, profile.label),
            "mail_subject": profile.mail_subject,
            "is_system": True,
        }
        for profile in WORK_TYPE_PROFILES.values()
    ]


def _normalise_custom_items(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for value in raw:
        if not isinstance(value, dict):
            continue
        key = str(value.get("key") or "").strip()
        name = str(value.get("name") or "").strip()
        mail_subject = str(value.get("mail_subject") or "").strip()
        if not key or not name or not mail_subject:
            continue
        items.append(
            {
                "key": key,
                "name": name,
                "mail_subject": mail_subject,
                "is_system": False,
            }
        )
    return items


def list_work_types(username: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        profile = session.get(UserProfile, username)
        custom = []
        if profile is not None:
            custom = _normalise_custom_items(
                (profile.mailing_defaults or {}).get(CUSTOM_WORK_TYPES_KEY)
            )
    return _system_items() + custom


def create_work_type(username: str, *, name: str, mail_subject: str) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    clean_subject = str(mail_subject or "").strip()
    if not clean_name:
        raise ValueError("Укажите название вида работ")
    if not clean_subject:
        raise ValueError("Укажите тему письма")
    if len(clean_name) > 128:
        raise ValueError("Название вида работ не должно превышать 128 символов")
    if len(clean_subject) > 512:
        raise ValueError("Тема письма не должна превышать 512 символов")

    with session_scope() as session:
        profile = session.get(UserProfile, username)
        if profile is None:
            profile = UserProfile(username=username)
            session.add(profile)
            session.flush()

        defaults = dict(profile.mailing_defaults or {})
        custom = _normalise_custom_items(defaults.get(CUSTOM_WORK_TYPES_KEY))
        existing_names = {
            item["name"].casefold()
            for item in (_system_items() + custom)
        }
        if clean_name.casefold() in existing_names:
            raise ValueError("Вид работ с таким названием уже существует")

        item = {
            "key": f"custom_{uuid4().hex[:16]}",
            "name": clean_name,
            "mail_subject": clean_subject,
            "is_system": False,
        }
        custom.append(item)
        defaults[CUSTOM_WORK_TYPES_KEY] = custom
        profile.mailing_defaults = defaults
        profile.updated_at = datetime.now(timezone.utc)
        session.flush()
        return item
