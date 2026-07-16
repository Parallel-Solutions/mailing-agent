"""Mail / KP / contract templates with versions."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import MailTemplate, TemplateVersion

VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _extract_variables(text: str) -> list[dict[str, Any]]:
    names = sorted(set(VARIABLE_RE.findall(text or "")))
    return [
        {
            "name": name,
            "source": "recipient" if name in {"company", "contact_name", "email", "region"} else "user_input",
            "label": name,
        }
        for name in names
    ]


def template_to_dict(row: MailTemplate, version: TemplateVersion | None = None) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "name": row.name,
        "template_type": row.template_type,
        "status": row.status,
        "active_version_id": row.active_version_id,
        "tags": list(row.tags or []),
        "archived": bool(row.archived),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if version is not None:
        payload["version"] = version_to_dict(version)
    return payload


def version_to_dict(row: TemplateVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "template_id": row.template_id,
        "version_number": row.version_number,
        "subject": row.subject,
        "body_html": row.body_html,
        "body_text": row.body_text,
        "variables": list(row.variables or []),
        "storage_key": row.storage_key,
        "filename": row.filename,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_template(
    owner_username: str,
    *,
    name: str,
    template_type: str,
    subject: str = "",
    body_html: str = "",
    body_text: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    template_id = _new_id()
    version_id = _new_id()
    combined = f"{subject}\n{body_html}\n{body_text}"
    with session_scope() as session:
        tmpl = MailTemplate(
            id=template_id,
            owner_username=owner_username,
            name=name or "Шаблон",
            template_type=template_type,
            status="ready",
            active_version_id=version_id,
            tags=list(tags or []),
        )
        session.add(tmpl)
        session.add(
            TemplateVersion(
                id=version_id,
                template_id=template_id,
                version_number=1,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                variables=_extract_variables(combined),
                created_by=owner_username,
            )
        )
        session.flush()
        return template_to_dict(tmpl, session.get(TemplateVersion, version_id))


def list_templates(
    owner_username: str,
    *,
    template_type: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(MailTemplate).where(MailTemplate.owner_username == owner_username)
        if not include_archived:
            stmt = stmt.where(MailTemplate.archived.is_(False))
        if template_type:
            stmt = stmt.where(MailTemplate.template_type == template_type)
        if q:
            stmt = stmt.where(MailTemplate.name.ilike(f"%{q.strip()}%"))
        rows = session.scalars(stmt.order_by(MailTemplate.updated_at.desc())).all()
        result = []
        for row in rows:
            version = session.get(TemplateVersion, row.active_version_id) if row.active_version_id else None
            result.append(template_to_dict(row, version))
        return result


def get_template(template_id: str, owner_username: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(MailTemplate, template_id)
        if row is None or row.owner_username != owner_username:
            return None
        version = session.get(TemplateVersion, row.active_version_id) if row.active_version_id else None
        return template_to_dict(row, version)


def save_version(
    template_id: str,
    owner_username: str,
    *,
    subject: str | None = None,
    body_html: str | None = None,
    body_text: str | None = None,
    variables: list[dict[str, Any]] | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username:
            return None
        current = session.get(TemplateVersion, tmpl.active_version_id) if tmpl.active_version_id else None
        next_number = (current.version_number + 1) if current else 1
        subj = subject if subject is not None else (current.subject if current else "")
        html = body_html if body_html is not None else (current.body_html if current else "")
        text = body_text if body_text is not None else (current.body_text if current else "")
        vars_list = variables if variables is not None else _extract_variables(f"{subj}\n{html}\n{text}")
        version_id = _new_id()
        session.add(
            TemplateVersion(
                id=version_id,
                template_id=template_id,
                version_number=next_number,
                subject=subj,
                body_html=html,
                body_text=text,
                variables=vars_list,
                storage_key=current.storage_key if current else None,
                filename=current.filename if current else None,
                created_by=owner_username,
            )
        )
        tmpl.active_version_id = version_id
        tmpl.status = "ready"
        if name is not None:
            tmpl.name = name
        tmpl.updated_at = _now()
        session.flush()
        return template_to_dict(tmpl, session.get(TemplateVersion, version_id))


def duplicate_template(template_id: str, owner_username: str) -> dict[str, Any] | None:
    source = get_template(template_id, owner_username)
    if not source:
        return None
    version = source.get("version") or {}
    return create_template(
        owner_username,
        name=f"{source['name']} (копия)",
        template_type=source["template_type"],
        subject=str(version.get("subject") or ""),
        body_html=str(version.get("body_html") or ""),
        body_text=str(version.get("body_text") or ""),
        tags=list(source.get("tags") or []),
    )


def archive_template(template_id: str, owner_username: str) -> dict[str, Any] | None:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username:
            return None
        tmpl.archived = True
        tmpl.updated_at = _now()
        session.flush()
        return template_to_dict(tmpl)


def list_versions(template_id: str, owner_username: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        tmpl = session.get(MailTemplate, template_id)
        if tmpl is None or tmpl.owner_username != owner_username:
            return []
        rows = session.scalars(
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template_id)
            .order_by(TemplateVersion.version_number.desc())
        ).all()
        return [version_to_dict(r) for r in rows]


def preview_template(template_id: str, owner_username: str, sample: dict[str, Any] | None = None) -> dict[str, Any] | None:
    tmpl = get_template(template_id, owner_username)
    if not tmpl:
        return None
    version = tmpl.get("version") or {}
    sample = sample or {
        "company": "ООО Пример",
        "contact_name": "Иван Иванов",
        "email": "ivan@example.com",
        "region": "Москва",
        "campaign_name": "Тестовая рассылка",
    }
    html = str(version.get("body_html") or "")
    subject = str(version.get("subject") or "")
    for key, value in sample.items():
        html = html.replace("{{" + key + "}}", str(value))
        subject = subject.replace("{{" + key + "}}", str(value))
    return {"subject": subject, "body_html": html, "sample": sample}
