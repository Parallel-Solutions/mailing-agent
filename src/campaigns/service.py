"""Campaign CRUD, recipients, schedule, launch, pause/resume/cancel."""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from src.campaigns.schedule_planner import plan_batches
from src.infra.db import session_scope
from src.infra.models import (
    Campaign,
    CampaignBatch,
    CampaignRecipient,
    CampaignSchedule,
    DeliveryAttempt,
)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

CAMPAIGN_STATUSES = {
    "draft",
    "scheduled",
    "running",
    "paused",
    "completed",
    "completed_with_errors",
    "cancelled",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _validate_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not email:
        return "empty"
    if not EMAIL_RE.match(email):
        return "invalid"
    return "valid"


def campaign_to_dict(row: Campaign, *, include_draft: bool = True) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "owner_username": row.owner_username,
        "job_id": row.job_id,
        "name": row.name,
        "work_type": row.work_type,
        "document_mode": row.document_mode,
        "mail_subject": row.mail_subject,
        "description": row.description,
        "send_scenario": row.send_scenario,
        "tags": list(row.tags or []),
        "internal_comment": row.internal_comment,
        "status": row.status,
        "smtp_mailbox_id": row.smtp_mailbox_id,
        "transport": row.transport,
        "email_template_id": row.email_template_id,
        "kp_template_id": row.kp_template_id,
        "contract_template_id": row.contract_template_id,
        "audience_id": row.audience_id,
        "sent_count": row.sent_count,
        "total_count": row.total_count,
        "error_count": row.error_count,
        "archived": bool(row.archived),
        "launched_at": row.launched_at.isoformat() if row.launched_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "progress": (
            round(100.0 * row.sent_count / row.total_count, 1) if row.total_count else 0.0
        ),
    }
    if include_draft:
        payload["draft_payload"] = dict(row.draft_payload or {})
    return payload


def create_campaign(owner_username: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or {}
    campaign_id = _new_id()
    with session_scope() as session:
        job_id = f"job-{campaign_id.replace('-', '')[:12]}"
        from src.jobs.access import assign_job_owner
        from src.jobs.storage import resolve_job_paths
        from src.security.auth import Principal

        resolve_job_paths(job_id).ensure_dirs()
        assign_job_owner(job_id, Principal(owner_username, "default", "user"), overwrite=True)

        row = Campaign(
            id=campaign_id,
            owner_username=owner_username,
            job_id=job_id,
            name=str(data.get("name") or "Черновик рассылки"),
            work_type=str(data.get("work_type") or ""),
            document_mode=str(data.get("document_mode") or "kp"),
            mail_subject=str(data.get("mail_subject") or ""),
            description=str(data.get("description") or ""),
            send_scenario=str(data.get("send_scenario") or "consent_then_materials"),
            tags=list(data.get("tags") or []),
            internal_comment=str(data.get("internal_comment") or ""),
            status="draft",
            smtp_mailbox_id=data.get("smtp_mailbox_id"),
            transport=str(data.get("transport") or "smtp"),
            draft_payload=dict(data.get("draft_payload") or data),
        )
        session.add(row)
        session.flush()
        schedule = CampaignSchedule(
            id=_new_id(),
            campaign_id=campaign_id,
            send_immediately=True,
            timezone="Europe/Moscow",
            weekdays=[0, 1, 2, 3, 4],
            time_windows=[{"start": "09:00", "end": "18:00"}],
            batch_size=25,
            interval_seconds=300,
        )
        session.add(schedule)
        session.flush()
        return campaign_to_dict(row)


def get_campaign(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Campaign, campaign_id)
        if row is None:
            return None
        if not is_admin and row.owner_username != owner_username:
            return None
        return campaign_to_dict(row)


def list_campaigns(
    owner_username: str,
    *,
    is_admin: bool = False,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> dict[str, Any]:
    with session_scope() as session:
        stmt = select(Campaign)
        if not is_admin:
            stmt = stmt.where(Campaign.owner_username == owner_username)
        if not include_archived:
            stmt = stmt.where(Campaign.archived.is_(False))
        if status:
            stmt = stmt.where(Campaign.status == status)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(Campaign.name.ilike(like))
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(stmt.order_by(Campaign.updated_at.desc()).limit(limit).offset(offset)).all()
        return {"items": [campaign_to_dict(r, include_draft=False) for r in rows], "total": int(total)}


def update_campaign(
    campaign_id: str,
    owner_username: str,
    data: dict[str, Any],
    *,
    is_admin: bool = False,
) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Campaign, campaign_id)
        if row is None:
            return None
        if not is_admin and row.owner_username != owner_username:
            return None
        if row.status not in {"draft", "scheduled", "paused"} and data.get("force") is not True:
            # Allow metadata updates on draft-like states; still allow draft_payload merge always for drafts
            if row.status not in {"draft"}:
                pass

        for field in (
            "name",
            "work_type",
            "document_mode",
            "mail_subject",
            "description",
            "send_scenario",
            "internal_comment",
            "smtp_mailbox_id",
            "transport",
            "email_template_id",
            "kp_template_id",
            "contract_template_id",
            "audience_id",
        ):
            if field in data and data[field] is not None:
                setattr(row, field, data[field])
        if "tags" in data:
            row.tags = list(data.get("tags") or [])
        if "draft_payload" in data and isinstance(data["draft_payload"], dict):
            merged = dict(row.draft_payload or {})
            merged.update(data["draft_payload"])
            row.draft_payload = merged
        # Merge top-level known fields into draft for autosave recovery
        draft = dict(row.draft_payload or {})
        for key in ("name", "work_type", "document_mode", "mail_subject", "description", "send_scenario", "tags", "internal_comment"):
            if key in data:
                draft[key] = data[key]
        row.draft_payload = draft
        row.updated_at = _now()
        session.flush()
        return campaign_to_dict(row)


def duplicate_campaign(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any] | None:
    source = get_campaign(campaign_id, owner_username, is_admin=is_admin)
    if not source:
        return None
    created = create_campaign(
        owner_username,
        {
            "name": f"{source['name']} (копия)",
            "work_type": source["work_type"],
            "document_mode": source["document_mode"],
            "mail_subject": source["mail_subject"],
            "description": source["description"],
            "send_scenario": source["send_scenario"],
            "tags": source["tags"],
            "internal_comment": source["internal_comment"],
            "smtp_mailbox_id": source["smtp_mailbox_id"],
            "transport": source["transport"],
            "draft_payload": source.get("draft_payload") or {},
        },
    )
    # Copy recipients
    with session_scope() as session:
        recipients = session.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        ).all()
        for idx, rec in enumerate(recipients):
            session.add(
                CampaignRecipient(
                    campaign_id=created["id"],
                    row_index=idx,
                    company=rec.company,
                    contact_name=rec.contact_name,
                    email=rec.email,
                    email_fallback=rec.email_fallback,
                    region=rec.region,
                    source=rec.source,
                    validation_status=rec.validation_status,
                    extra=dict(rec.extra or {}),
                    excluded=rec.excluded,
                )
            )
        camp = session.get(Campaign, created["id"])
        if camp:
            camp.total_count = len(recipients)
            camp.email_template_id = source.get("email_template_id")
            camp.kp_template_id = source.get("kp_template_id")
            camp.contract_template_id = source.get("contract_template_id")
    return get_campaign(created["id"], owner_username, is_admin=is_admin)


def archive_campaign(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Campaign, campaign_id)
        if row is None or (not is_admin and row.owner_username != owner_username):
            return None
        row.archived = True
        row.updated_at = _now()
        session.flush()
        return campaign_to_dict(row)


def list_recipients(
    campaign_id: str,
    owner_username: str,
    *,
    is_admin: bool = False,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    only_excluded: bool | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            return {"items": [], "total": 0}
        stmt = select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                (CampaignRecipient.email.ilike(like))
                | (CampaignRecipient.company.ilike(like))
                | (CampaignRecipient.contact_name.ilike(like))
            )
        if only_excluded is True:
            stmt = stmt.where(CampaignRecipient.excluded.is_(True))
        elif only_excluded is False:
            stmt = stmt.where(CampaignRecipient.excluded.is_(False))
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(stmt.order_by(CampaignRecipient.row_index).limit(limit).offset(offset)).all()
        items = [
            {
                "id": r.id,
                "row_index": r.row_index,
                "company": r.company,
                "contact_name": r.contact_name,
                "email": r.email,
                "email_fallback": r.email_fallback,
                "region": r.region,
                "source": r.source,
                "validation_status": r.validation_status,
                "extra": dict(r.extra or {}),
                "excluded": r.excluded,
                "send_status": r.send_status,
                "last_error": r.last_error,
            }
            for r in rows
        ]
        return {"items": items, "total": int(total)}


def replace_recipients(
    campaign_id: str,
    owner_username: str,
    recipients: list[dict[str, Any]],
    *,
    is_admin: bool = False,
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")
        if camp.status not in {"draft", "paused", "scheduled"}:
            raise ValueError("Cannot replace recipients for campaign in status " + camp.status)

        for old in session.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        ).all():
            session.delete(old)
        seen_emails: set[str] = set()
        added = 0
        duplicates = 0
        invalid = 0
        for idx, item in enumerate(recipients):
            email = str(item.get("email") or "").strip().lower()
            status = _validate_email(email)
            if status == "invalid":
                invalid += 1
            if email and email in seen_emails:
                duplicates += 1
                continue
            if email:
                seen_emails.add(email)
            session.add(
                CampaignRecipient(
                    campaign_id=campaign_id,
                    row_index=added,
                    company=str(item.get("company") or ""),
                    contact_name=str(item.get("contact_name") or item.get("contact") or ""),
                    email=email,
                    email_fallback=str(item.get("email_fallback") or "").strip().lower(),
                    region=str(item.get("region") or ""),
                    source=str(item.get("source") or "import"),
                    validation_status=status,
                    extra=dict(item.get("extra") or {}),
                    excluded=bool(item.get("excluded") or status != "valid"),
                )
            )
            added += 1
        camp.total_count = added
        camp.updated_at = _now()
        session.flush()
        return {
            "total": added,
            "duplicates_skipped": duplicates,
            "invalid": invalid,
        }


def update_recipient(
    campaign_id: str,
    recipient_id: int,
    owner_username: str,
    data: dict[str, Any],
    *,
    is_admin: bool = False,
) -> dict[str, Any] | None:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            return None
        row = session.get(CampaignRecipient, recipient_id)
        if row is None or row.campaign_id != campaign_id:
            return None
        for field in ("company", "contact_name", "email", "email_fallback", "region", "source"):
            if field in data and data[field] is not None:
                setattr(row, field, data[field] if field not in {"email", "email_fallback"} else str(data[field]).strip().lower())
        if "excluded" in data:
            row.excluded = bool(data["excluded"])
        if "extra" in data and isinstance(data["extra"], dict):
            row.extra = dict(data["extra"])
        row.validation_status = _validate_email(row.email)
        if row.validation_status != "valid":
            row.excluded = True
        session.flush()
        return {
            "id": row.id,
            "email": row.email,
            "validation_status": row.validation_status,
            "excluded": row.excluded,
        }


def delete_recipients(
    campaign_id: str,
    recipient_ids: list[int],
    owner_username: str,
    *,
    is_admin: bool = False,
) -> int:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            return 0
        deleted = 0
        for rid in recipient_ids:
            row = session.get(CampaignRecipient, rid)
            if row and row.campaign_id == campaign_id:
                session.delete(row)
                deleted += 1
        remaining = session.scalar(
            select(func.count()).select_from(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        ) or 0
        camp.total_count = int(remaining)
        return deleted


def parse_recipients_csv(content: bytes) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        normalized = {str(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        rows.append(
            {
                "company": normalized.get("company") or normalized.get("компания") or normalized.get("organization") or "",
                "contact_name": normalized.get("contact") or normalized.get("contact_name") or normalized.get("контакт") or "",
                "email": normalized.get("email") or normalized.get("e-mail") or normalized.get("почта") or "",
                "email_fallback": normalized.get("email_fallback") or normalized.get("email2") or "",
                "region": normalized.get("region") or normalized.get("регион") or "",
                "source": "csv",
            }
        )
    return rows


def parse_recipients_xlsx(content: bytes) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(c or "").strip().lower() for c in next(rows_iter, [])]
    mapping = {h: i for i, h in enumerate(headers)}

    def cell(row: tuple[Any, ...], *names: str) -> str:
        for name in names:
            idx = mapping.get(name)
            if idx is not None and idx < len(row):
                return str(row[idx] or "").strip()
        return ""

    result: list[dict[str, Any]] = []
    for row in rows_iter:
        if not row:
            continue
        email = cell(row, "email", "e-mail", "почта")
        company = cell(row, "company", "компания", "organization")
        if not email and not company:
            continue
        result.append(
            {
                "company": company,
                "contact_name": cell(row, "contact", "contact_name", "контакт"),
                "email": email,
                "email_fallback": cell(row, "email_fallback", "email2"),
                "region": cell(row, "region", "регион"),
                "source": "xlsx",
            }
        )
    return result


def upsert_schedule(
    campaign_id: str,
    owner_username: str,
    data: dict[str, Any],
    *,
    is_admin: bool = False,
) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")
        schedule = session.scalar(select(CampaignSchedule).where(CampaignSchedule.campaign_id == campaign_id))
        if schedule is None:
            schedule = CampaignSchedule(id=_new_id(), campaign_id=campaign_id)
            session.add(schedule)

        if "send_immediately" in data:
            schedule.send_immediately = bool(data["send_immediately"])
        if "start_at" in data:
            value = data["start_at"]
            if value in (None, ""):
                schedule.start_at = None
            elif isinstance(value, datetime):
                schedule.start_at = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            else:
                schedule.start_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        for field in ("timezone", "on_error"):
            if field in data and data[field] is not None:
                setattr(schedule, field, str(data[field]))
        for field in ("batch_size", "interval_seconds", "pause_between_messages_ms", "max_per_hour", "max_per_day", "max_retries"):
            if field in data and data[field] is not None:
                setattr(schedule, field, int(data[field]))
        if "weekdays" in data:
            schedule.weekdays = list(data["weekdays"] or [])
        if "time_windows" in data:
            schedule.time_windows = list(data["time_windows"] or [])

        active_count = session.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.excluded.is_(False),
            )
        ) or 0
        preview = plan_batches(
            recipient_count=int(active_count),
            batch_size=schedule.batch_size,
            interval_seconds=schedule.interval_seconds,
            start_at=schedule.start_at,
            send_immediately=schedule.send_immediately,
            timezone_name=schedule.timezone,
            weekdays=list(schedule.weekdays or []),
            time_windows=list(schedule.time_windows or []),
            max_per_hour=schedule.max_per_hour,
            max_per_day=schedule.max_per_day,
        )
        schedule.preview = preview
        schedule.updated_at = _now()
        session.flush()
        return schedule_to_dict(schedule)


def schedule_to_dict(schedule: CampaignSchedule) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "campaign_id": schedule.campaign_id,
        "send_immediately": schedule.send_immediately,
        "start_at": schedule.start_at.isoformat() if schedule.start_at else None,
        "timezone": schedule.timezone,
        "weekdays": list(schedule.weekdays or []),
        "time_windows": list(schedule.time_windows or []),
        "batch_size": schedule.batch_size,
        "interval_seconds": schedule.interval_seconds,
        "pause_between_messages_ms": schedule.pause_between_messages_ms,
        "max_per_hour": schedule.max_per_hour,
        "max_per_day": schedule.max_per_day,
        "on_error": schedule.on_error,
        "max_retries": schedule.max_retries,
        "preview": dict(schedule.preview or {}),
    }


def get_schedule(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any] | None:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            return None
        schedule = session.scalar(select(CampaignSchedule).where(CampaignSchedule.campaign_id == campaign_id))
        if schedule is None:
            return None
        return schedule_to_dict(schedule)


def validate_campaign_for_launch(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            return {"ok": False, "errors": ["Рассылка не найдена"], "warnings": []}
        errors: list[str] = []
        warnings: list[str] = []
        if not (camp.name or "").strip():
            errors.append("Укажите название рассылки")
        if not (camp.mail_subject or "").strip():
            errors.append("Укажите тему письма")
        from src.campaigns.connection_service import validate_connection_choice

        connection_error = validate_connection_choice(
            camp.smtp_mailbox_id, camp.owner_username, camp.transport
        )
        if connection_error:
            errors.append(connection_error)
        active = session.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign_id, CampaignRecipient.excluded.is_(False))
        ) or 0
        if active <= 0:
            errors.append("Нет получателей для отправки")
        if not camp.email_template_id and not (camp.draft_payload or {}).get("email_body"):
            warnings.append("Шаблон письма не выбран — будет использован текст по умолчанию")
        schedule = session.scalar(select(CampaignSchedule).where(CampaignSchedule.campaign_id == campaign_id))
        return {
            "ok": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "active_recipients": int(active),
            "excluded_recipients": int(
                session.scalar(
                    select(func.count())
                    .select_from(CampaignRecipient)
                    .where(CampaignRecipient.campaign_id == campaign_id, CampaignRecipient.excluded.is_(True))
                )
                or 0
            ),
            "schedule": schedule_to_dict(schedule) if schedule else None,
            "campaign": campaign_to_dict(camp),
        }


def launch_campaign(
    campaign_id: str,
    owner_username: str,
    *,
    is_admin: bool = False,
    force_now: bool = False,
) -> dict[str, Any]:
    validation = validate_campaign_for_launch(campaign_id, owner_username, is_admin=is_admin)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))

    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        assert camp is not None
        schedule = session.scalar(select(CampaignSchedule).where(CampaignSchedule.campaign_id == campaign_id))
        assert schedule is not None

        # Clear future batches if re-launch from paused/scheduled
        existing = session.scalars(
            select(CampaignBatch).where(
                CampaignBatch.campaign_id == campaign_id,
                CampaignBatch.status.in_(["pending", "paused"]),
            )
        ).all()
        for batch in existing:
            session.delete(batch)

        recipients = session.scalars(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.excluded.is_(False),
                CampaignRecipient.send_status.in_(["pending", "failed"]),
            )
            .order_by(CampaignRecipient.row_index)
        ).all()
        recipient_ids = [r.id for r in recipients]
        # force_now bypasses calendar windows so "Запустить сейчас" is immediate.
        preview = plan_batches(
            recipient_count=len(recipient_ids),
            batch_size=schedule.batch_size,
            interval_seconds=0 if force_now else schedule.interval_seconds,
            start_at=None if force_now else schedule.start_at,
            send_immediately=True if force_now else schedule.send_immediately,
            timezone_name=schedule.timezone,
            weekdays=[] if force_now else list(schedule.weekdays or []),
            time_windows=[] if force_now else list(schedule.time_windows or []),
            max_per_hour=0 if force_now else schedule.max_per_hour,
            max_per_day=0 if force_now else schedule.max_per_day,
        )
        schedule.preview = preview

        from src.workers.task_queue import enqueue_task

        created_batches: list[dict[str, Any]] = []
        offset = 0
        for plan in preview.get("batches") or []:
            size = int(plan["size"])
            chunk = recipient_ids[offset : offset + size]
            offset += size
            batch_id = _new_id()
            scheduled_at = datetime.fromisoformat(str(plan["scheduled_at"]).replace("Z", "+00:00"))
            batch = CampaignBatch(
                id=batch_id,
                campaign_id=campaign_id,
                batch_index=int(plan["batch_index"]),
                scheduled_at=scheduled_at,
                size=len(chunk),
                status="pending",
                recipient_ids=chunk,
            )
            session.add(batch)
            session.flush()

            # Idempotent task per batch
            task, _created = enqueue_task(
                task_type="sender_batch",
                job_id=camp.job_id or campaign_id,
                owner_username=owner_username,
                payload={
                    "campaign_id": campaign_id,
                    "batch_id": batch_id,
                    "smtp_mailbox_id": camp.smtp_mailbox_id,
                    "transport": camp.transport,
                    "mail_subject": camp.mail_subject,
                    "send_mode": (
                        "consent_request"
                        if camp.send_scenario == "consent_then_materials"
                        else "materials"
                    ),
                    "campaign_name": camp.name,
                    "pause_between_messages_ms": schedule.pause_between_messages_ms,
                    "max_retries": schedule.max_retries,
                    "on_error": schedule.on_error,
                },
                available_at=scheduled_at,
                idempotency_key=f"sender_batch:{batch_id}",
                active_key=f"sender_batch:{batch_id}",
                max_attempts=max(1, int(schedule.max_retries or 3)),
            )
            batch.task_id = str(task.get("id") or "")
            created_batches.append(
                {
                    "id": batch_id,
                    "batch_index": batch.batch_index,
                    "scheduled_at": scheduled_at.isoformat(),
                    "size": batch.size,
                    "task_id": batch.task_id,
                }
            )

        camp.status = "running" if (force_now or schedule.send_immediately) else "scheduled"
        if camp.status == "running" or force_now:
            # If first batch is in the future, keep scheduled
            first_at = preview.get("first_batch_at")
            if first_at:
                first_dt = datetime.fromisoformat(str(first_at).replace("Z", "+00:00"))
                if first_dt > _now() + __import__("datetime").timedelta(seconds=5):
                    camp.status = "scheduled"
        camp.launched_at = _now()
        camp.total_count = len(recipient_ids)
        camp.updated_at = _now()
        session.flush()

        # Heal missing JobOwner so statistics scoping matches Campaign ownership.
        if camp.job_id:
            from src.jobs.access import assign_job_owner
            from src.security.auth import Principal

            assign_job_owner(
                camp.job_id,
                Principal(camp.owner_username, "default", "user"),
                overwrite=False,
            )

        return {
            "campaign": campaign_to_dict(camp),
            "batches": created_batches,
            "preview": preview,
        }


def pause_campaign(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")
        camp.status = "paused"
        camp.updated_at = _now()
        batches = session.scalars(
            select(CampaignBatch).where(
                CampaignBatch.campaign_id == campaign_id,
                CampaignBatch.status == "pending",
            )
        ).all()
        from src.workers.task_queue import request_cancel

        for batch in batches:
            batch.status = "paused"
            if batch.task_id:
                try:
                    request_cancel(batch.task_id)
                except Exception:
                    pass
        session.flush()
        return campaign_to_dict(camp)


def resume_campaign(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")
        if camp.status != "paused":
            raise ValueError("Campaign is not paused")

        from src.workers.task_queue import enqueue_task

        batches = session.scalars(
            select(CampaignBatch)
            .where(CampaignBatch.campaign_id == campaign_id, CampaignBatch.status == "paused")
            .order_by(CampaignBatch.batch_index)
        ).all()
        now = _now()
        for batch in batches:
            scheduled_at = batch.scheduled_at if batch.scheduled_at > now else now
            batch.status = "pending"
            batch.scheduled_at = scheduled_at
            task, _created = enqueue_task(
                task_type="sender_batch",
                job_id=camp.job_id or campaign_id,
                owner_username=owner_username,
                payload={
                    "campaign_id": campaign_id,
                    "batch_id": batch.id,
                    "smtp_mailbox_id": camp.smtp_mailbox_id,
                    "transport": camp.transport,
                    "mail_subject": camp.mail_subject,
                    "send_mode": (
                        "consent_request"
                        if camp.send_scenario == "consent_then_materials"
                        else "materials"
                    ),
                    "campaign_name": camp.name,
                },
                available_at=scheduled_at,
                idempotency_key=f"sender_batch:{batch.id}:resume:{int(now.timestamp())}",
                active_key=f"sender_batch:{batch.id}:resume:{int(now.timestamp())}",
            )
            batch.task_id = str(task.get("id") or "")
        camp.status = "running"
        camp.updated_at = now
        session.flush()
        return campaign_to_dict(camp)


def cancel_campaign(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")
        camp.status = "cancelled"
        camp.completed_at = _now()
        camp.updated_at = _now()
        from src.workers.task_queue import request_cancel

        batches = session.scalars(
            select(CampaignBatch).where(
                CampaignBatch.campaign_id == campaign_id,
                CampaignBatch.status.in_(["pending", "paused", "running"]),
            )
        ).all()
        for batch in batches:
            if batch.status != "running":
                batch.status = "cancelled"
            if batch.task_id:
                try:
                    request_cancel(batch.task_id)
                except Exception:
                    pass
        session.flush()
        return campaign_to_dict(camp)


def list_batches(campaign_id: str, owner_username: str, *, is_admin: bool = False) -> list[dict[str, Any]]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            return []
        rows = session.scalars(
            select(CampaignBatch).where(CampaignBatch.campaign_id == campaign_id).order_by(CampaignBatch.batch_index)
        ).all()
        return [
            {
                "id": b.id,
                "batch_index": b.batch_index,
                "scheduled_at": b.scheduled_at.isoformat() if b.scheduled_at else None,
                "size": b.size,
                "sent_count": b.sent_count,
                "error_count": b.error_count,
                "remaining": max(0, b.size - b.sent_count - b.error_count),
                "status": b.status,
                "task_id": b.task_id,
                "error": b.error,
                "started_at": b.started_at.isoformat() if b.started_at else None,
                "completed_at": b.completed_at.isoformat() if b.completed_at else None,
            }
            for b in rows
        ]


def cancel_batch(campaign_id: str, batch_id: str, owner_username: str, *, is_admin: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None or (not is_admin and camp.owner_username != owner_username):
            raise PermissionError("Campaign not found")
        batch = session.get(CampaignBatch, batch_id)
        if batch is None or batch.campaign_id != campaign_id:
            raise PermissionError("Batch not found")
        if batch.status not in {"pending", "paused"}:
            raise ValueError("Only future batches can be cancelled")
        batch.status = "cancelled"
        if batch.task_id:
            from src.workers.task_queue import request_cancel

            request_cancel(batch.task_id)
        session.flush()
        return {"id": batch.id, "status": batch.status}


def active_sending(owner_username: str, *, is_admin: bool = False) -> dict[str, Any] | None:
    with session_scope() as session:
        stmt = select(Campaign).where(Campaign.status.in_(["running", "scheduled", "paused"]))
        if not is_admin:
            stmt = stmt.where(Campaign.owner_username == owner_username)
        camp = session.scalars(stmt.order_by(Campaign.updated_at.desc()).limit(1)).first()
        if camp is None:
            return None
        schedule = session.scalar(select(CampaignSchedule).where(CampaignSchedule.campaign_id == camp.id))
        next_batch = session.scalars(
            select(CampaignBatch)
            .where(CampaignBatch.campaign_id == camp.id, CampaignBatch.status == "pending")
            .order_by(CampaignBatch.scheduled_at)
            .limit(1)
        ).first()
        running_batch = session.scalars(
            select(CampaignBatch)
            .where(CampaignBatch.campaign_id == camp.id, CampaignBatch.status == "running")
            .limit(1)
        ).first()
        queued = session.scalar(
            select(func.count())
            .select_from(CampaignBatch)
            .where(CampaignBatch.campaign_id == camp.id, CampaignBatch.status == "pending")
        ) or 0
        return {
            "campaign_id": camp.id,
            "name": camp.name,
            "status": camp.status,
            "sent_count": camp.sent_count,
            "total_count": camp.total_count,
            "remaining": max(0, camp.total_count - camp.sent_count),
            "queued_batches": int(queued),
            "sending_now": running_batch.size if running_batch else 0,
            "next_batch_size": next_batch.size if next_batch else 0,
            "next_batch_at": next_batch.scheduled_at.isoformat() if next_batch else None,
            "batch_size": schedule.batch_size if schedule else 0,
            "interval_seconds": schedule.interval_seconds if schedule else 0,
            "max_per_hour": schedule.max_per_hour if schedule else 0,
            "max_per_day": schedule.max_per_day if schedule else 0,
            "progress": round(100.0 * camp.sent_count / camp.total_count, 1) if camp.total_count else 0.0,
        }


def record_delivery_attempt(
    *,
    campaign_id: str,
    recipient_id: int,
    batch_id: str | None,
    status: str,
    error: str | None = None,
    provider_message_id: str | None = None,
) -> bool:
    """Return False if already recorded (idempotent skip)."""
    key = f"{campaign_id}:{recipient_id}:1"
    with session_scope() as session:
        existing = session.scalar(select(DeliveryAttempt).where(DeliveryAttempt.idempotency_key == key))
        if existing and existing.status in {"sent", "delivered"}:
            return False
        if existing is None:
            session.add(
                DeliveryAttempt(
                    campaign_id=campaign_id,
                    recipient_id=recipient_id,
                    batch_id=batch_id,
                    attempt_number=1,
                    status=status,
                    error=error,
                    provider_message_id=provider_message_id,
                    idempotency_key=key,
                )
            )
        else:
            existing.status = status
            existing.error = error
            existing.provider_message_id = provider_message_id
            existing.updated_at = _now()
        return True
