"""Saved audiences and members."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from src.campaigns.service import _validate_email
from src.infra.db import session_scope
from src.infra.models import Audience, AudienceMember


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def audience_to_dict(row: Audience) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "source": row.source,
        "member_count": row.member_count,
        "quality_score": row.quality_score,
        "meta": dict(row.meta or {}),
        "archived": bool(row.archived),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def create_audience(owner_username: str, name: str, *, source: str = "manual") -> dict[str, Any]:
    with session_scope() as session:
        row = Audience(id=_new_id(), owner_username=owner_username, name=name or "Аудитория", source=source)
        session.add(row)
        session.flush()
        return audience_to_dict(row)


def list_audiences(owner_username: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(Audience).where(Audience.owner_username == owner_username)
        if not include_archived:
            stmt = stmt.where(Audience.archived.is_(False))
        rows = session.scalars(stmt.order_by(Audience.updated_at.desc())).all()
        return [audience_to_dict(r) for r in rows]


def get_audience(audience_id: str, owner_username: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Audience, audience_id)
        if row is None or row.owner_username != owner_username:
            return None
        return audience_to_dict(row)


def update_audience(audience_id: str, owner_username: str, data: dict[str, Any]) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Audience, audience_id)
        if row is None or row.owner_username != owner_username:
            return None
        if "name" in data and data["name"] is not None:
            row.name = str(data["name"])
        if "archived" in data:
            row.archived = bool(data["archived"])
        row.updated_at = _now()
        session.flush()
        return audience_to_dict(row)


def duplicate_audience(audience_id: str, owner_username: str) -> dict[str, Any] | None:
    source = get_audience(audience_id, owner_username)
    if not source:
        return None
    created = create_audience(owner_username, f"{source['name']} (копия)", source=source["source"])
    members = list_members(audience_id, owner_username, limit=100_000, offset=0)
    replace_members(created["id"], owner_username, members["items"])
    return get_audience(created["id"], owner_username)


def list_members(
    audience_id: str,
    owner_username: str,
    *,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        aud = session.get(Audience, audience_id)
        if aud is None or aud.owner_username != owner_username:
            return {"items": [], "total": 0}
        stmt = select(AudienceMember).where(AudienceMember.audience_id == audience_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                (AudienceMember.email.ilike(like))
                | (AudienceMember.company.ilike(like))
                | (AudienceMember.contact_name.ilike(like))
            )
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(stmt.order_by(AudienceMember.id).limit(limit).offset(offset)).all()
        return {
            "items": [
                {
                    "id": r.id,
                    "company": r.company,
                    "contact_name": r.contact_name,
                    "email": r.email,
                    "email_fallback": r.email_fallback,
                    "region": r.region,
                    "source": r.source,
                    "validation_status": r.validation_status,
                    "extra": dict(r.extra or {}),
                    "excluded": r.excluded,
                }
                for r in rows
            ],
            "total": int(total),
        }


def replace_members(audience_id: str, owner_username: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    with session_scope() as session:
        aud = session.get(Audience, audience_id)
        if aud is None or aud.owner_username != owner_username:
            raise PermissionError("Audience not found")
        for old in session.scalars(select(AudienceMember).where(AudienceMember.audience_id == audience_id)).all():
            session.delete(old)
        valid = 0
        for item in members:
            email = str(item.get("email") or "").strip().lower()
            status = _validate_email(email)
            if status == "valid":
                valid += 1
            session.add(
                AudienceMember(
                    audience_id=audience_id,
                    company=str(item.get("company") or ""),
                    contact_name=str(item.get("contact_name") or ""),
                    email=email,
                    email_fallback=str(item.get("email_fallback") or "").strip().lower(),
                    region=str(item.get("region") or ""),
                    source=str(item.get("source") or "import"),
                    validation_status=status,
                    extra=dict(item.get("extra") or {}),
                    excluded=bool(item.get("excluded") or status != "valid"),
                )
            )
        aud.member_count = len(members)
        aud.quality_score = round(100.0 * valid / len(members), 1) if members else 0.0
        aud.updated_at = _now()
        session.flush()
        return {"member_count": aud.member_count, "quality_score": aud.quality_score}


def copy_audience_to_campaign(audience_id: str, campaign_id: str, owner_username: str) -> dict[str, Any]:
    from src.campaigns.service import replace_recipients

    members = list_members(audience_id, owner_username, limit=100_000, offset=0)
    return replace_recipients(campaign_id, owner_username, members["items"])
