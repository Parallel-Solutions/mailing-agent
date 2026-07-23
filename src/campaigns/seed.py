"""Deterministic demo seed for local manual testing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet

from src.campaigns import audience_service, profile_service, service, template_service
from src.generator.delivery.smtp_mailboxes import create_mailbox, list_mailboxes
import uuid

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignBatch, CampaignRecipient
from src.jobs.job_docs import append_event, read_sent_mail_log
from src.security.user_store import UserStoreError, create_user, get_user_record
from src.utils.config import settings
from src.utils.logger import logger

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-pass-123"


def _ensure_campaign_sent_mail_log(campaign_id: str) -> int:
    """Write sent_mail_log events for seeded sent/failed recipients (idempotent)."""
    with session_scope() as session:
        camp = session.get(Campaign, campaign_id)
        if camp is None:
            return 0
        job_id = str(camp.job_id or "").strip()
        if not job_id:
            return 0
        campaign_name = str(camp.name or "")
        subject = str(camp.mail_subject or "")
        recipients = list(
            session.scalars(
                select(CampaignRecipient)
                .where(CampaignRecipient.campaign_id == campaign_id)
                .order_by(CampaignRecipient.row_index)
            ).all()
        )
        payload_rows: list[dict[str, Any]] = []
        for rec in recipients:
            status = str(rec.send_status or "").strip().lower()
            if status not in {"sent", "failed"}:
                continue
            email = str(rec.email or "").strip()
            if not email:
                continue
            row_id = str(rec.row_index + 1 if rec.row_index is not None else rec.id)
            record: dict[str, Any] = {
                "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "transport": "smtp",
                "row_id": row_id,
                "mun_name": str(rec.company or ""),
                "recipient": email,
                "email": email,
                "organization": str(rec.company or ""),
                "subject": subject,
                "campaign_name": campaign_name,
                "campaign_id": campaign_id,
                "status": status,
                "send_mode": "materials",
                "attachments": [],
                "attachment_paths": [],
            }
            if status == "failed":
                err = str(rec.last_error or "SMTP rejected").strip() or "SMTP rejected"
                record["warning"] = err
                record["error"] = err
            payload_rows.append(record)

    if not payload_rows:
        return 0
    if read_sent_mail_log(job_id):
        return 0
    written = 0
    for record in payload_rows:
        append_event(
            job_id,
            "sent_mail_log",
            record,
            idempotency_key=f"seed:{campaign_id}:{record['recipient']}:{record['status']}",
        )
        written += 1
    return written


def _backfill_sent_mail_logs_for_user(username: str) -> int:
    """Ensure demo campaigns with sent/failed recipients appear in statistics."""
    listed = service.list_campaigns(username, limit=200)
    total = 0
    for item in listed.get("items") or []:
        campaign_id = str(item.get("id") or "").strip()
        if campaign_id:
            total += _ensure_campaign_sent_mail_log(campaign_id)
    return total


def ensure_demo_user() -> str:
    if get_user_record(DEMO_USERNAME) is None:
        try:
            create_user(DEMO_USERNAME, DEMO_PASSWORD)
        except UserStoreError:
            pass
    return DEMO_USERNAME


def ensure_smtp_key() -> None:
    if not str(getattr(settings, "smtp_credentials_key", "") or "").strip():
        # Ephemeral key only for empty local configs; prefer env in real deployments.
        settings.smtp_credentials_key = Fernet.generate_key().decode("ascii")


def _seed_for_user(username: str, *, force: bool = False) -> dict[str, Any]:
    profile_service.update_profile(
        username,
        {
            "display_name": "Demo Manager",
            "email": "demo@example.com",
            "company": "ООО Демо",
            "job_title": "Менеджер рассылок",
            "signature": "С уважением,\nDemo Manager",
            "timezone": "Europe/Moscow",
            "mailing_defaults": {"default_document_mode": "kp", "default_batch_size": "10"},
            "notifications": {"email_on_complete": True, "email_on_error": True},
        },
    )

    mailboxes = list_mailboxes(username)
    mailpit_id = None
    secondary_id = None
    for box in mailboxes:
        if box.get("host") == "mailpit" or "mailpit" in str(box.get("email") or ""):
            mailpit_id = box["id"]
        else:
            secondary_id = box["id"]
    if not mailpit_id:
        mailpit = create_mailbox(
            owner_username=username,
            provider="custom",
            email="sender@mailpit.local",
            password="mailpit",
            sender_name="ai-offer Demo",
            host="mailpit",
            port=1025,
            use_ssl=False,
            use_starttls=False,
            make_default=True,
        )
        mailpit_id = mailpit["id"]
    if not secondary_id:
        secondary = create_mailbox(
            owner_username=username,
            provider="custom",
            email="backup@mailpit.local",
            password="mailpit",
            sender_name="Backup Sender",
            host="mailpit",
            port=1025,
            use_ssl=False,
            use_starttls=False,
            make_default=False,
        )
        secondary_id = secondary["id"]

    existing = service.list_campaigns(username, limit=5)
    if existing["total"] > 0 and not force:
        backfilled = _backfill_sent_mail_logs_for_user(username)
        logger.info(
            "seed_demo_data_skipped",
            reason="campaigns_already_exist",
            username=username,
            total=existing["total"],
            sent_mail_log_backfilled=backfilled,
        )
        return {
            "skipped": True,
            "username": username,
            "mailpit_mailbox_id": mailpit_id,
            "sent_mail_log_backfilled": backfilled,
        }

    email_tmpl = template_service.create_template(
        username,
        name="Приветственное письмо",
        template_type="email",
        subject="Предложение для {{company}}",
        body_html="<p>Здравствуйте, {{contact_name}}!</p><p>Готовим материалы для {{company}}.</p>",
    )
    kp_tmpl = template_service.create_template(
        username,
        name="Шаблон КП",
        template_type="kp",
        subject="КП",
        body_html="<p>Коммерческое предложение для {{company}}</p>",
    )
    contract_tmpl = template_service.create_template(
        username,
        name="Шаблон договора",
        template_type="contract",
        subject="Договор",
        body_html="<p>Договор с {{company}}</p>",
    )

    audience = audience_service.create_audience(username, "Демо аудитория", source="seed")
    audience_service.replace_members(
        audience["id"],
        username,
        [
            {
                "company": "ООО Альфа",
                "contact_name": "Анна Альфа",
                "email": "alpha@example.com",
                "region": "Москва",
                "source": "seed",
            },
            {
                "company": "ООО Бета",
                "contact_name": "Борис Бета",
                "email": "beta@example.com",
                "region": "СПб",
                "source": "seed",
            },
            {
                "company": "ООО Гамма",
                "contact_name": "Галина",
                "email": "not-an-email",
                "region": "Казань",
                "source": "seed",
            },
        ],
    )
    audience2 = audience_service.create_audience(username, "Регион Центр", source="seed")
    audience_service.replace_members(
        audience2["id"],
        username,
        [
            {
                "company": "ООО Дельта",
                "contact_name": "Дмитрий",
                "email": "delta@example.com",
                "region": "Тула",
                "source": "seed",
            }
        ],
    )

    draft = service.create_campaign(
        username,
        {
            "name": "Черновик демо",
            "work_type": "Аудит",
            "document_mode": "kp",
            "mail_subject": "Черновик темы",
            "description": "Демо черновик",
            "send_scenario": "consent_then_materials",
            "smtp_mailbox_id": mailpit_id,
            "email_template_id": email_tmpl["id"],
        },
    )
    service.replace_recipients(
        draft["id"],
        username,
        [
            {
                "company": "ООО Черновик",
                "contact_name": "Чернов",
                "email": "draft@example.com",
                "region": "Москва",
            }
        ],
    )

    scheduled = service.create_campaign(
        username,
        {
            "name": "Запланированная демо",
            "mail_subject": "Старт завтра",
            "document_mode": "both",
            "smtp_mailbox_id": mailpit_id,
            "email_template_id": email_tmpl["id"],
            "kp_template_id": kp_tmpl["id"],
            "contract_template_id": contract_tmpl["id"],
            "send_scenario": "materials_now",
        },
    )
    service.replace_recipients(
        scheduled["id"],
        username,
        [
            {"company": "ООО План", "contact_name": "Платонов", "email": "plan1@example.com"},
            {"company": "ООО План 2", "contact_name": "Петрова", "email": "plan2@example.com"},
        ],
    )
    start_at = datetime.now(timezone.utc) + timedelta(hours=2)
    service.upsert_schedule(
        scheduled["id"],
        username,
        {
            "send_immediately": False,
            "start_at": start_at.isoformat(),
            "batch_size": 1,
            "interval_seconds": 60,
            "weekdays": [0, 1, 2, 3, 4],
            "time_windows": [{"start": "09:00", "end": "18:00"}],
        },
    )
    # Mark as scheduled without actually launching distant batches in seed if launch would enqueue now
    with session_scope() as session:
        row = session.get(Campaign, scheduled["id"])
        if row:
            row.status = "scheduled"
            row.launched_at = datetime.now(timezone.utc)

    completed = service.create_campaign(
        username,
        {
            "name": "Завершённая демо",
            "mail_subject": "Готово",
            "smtp_mailbox_id": mailpit_id,
            "email_template_id": email_tmpl["id"],
        },
    )
    service.replace_recipients(
        completed["id"],
        username,
        [
            {"company": "ООО Готово", "contact_name": "Готов", "email": "done@example.com"},
        ],
    )
    with session_scope() as session:
        row = session.get(Campaign, completed["id"])
        if row:
            row.status = "completed"
            row.sent_count = 1
            row.total_count = 1
            row.completed_at = datetime.now(timezone.utc)
        for rec in session.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == completed["id"])
        ).all():
            rec.send_status = "sent"
    _ensure_campaign_sent_mail_log(completed["id"])

    errored = service.create_campaign(
        username,
        {
            "name": "Демо с ошибками",
            "mail_subject": "Ошибки",
            "smtp_mailbox_id": secondary_id,
            "email_template_id": email_tmpl["id"],
        },
    )
    service.replace_recipients(
        errored["id"],
        username,
        [
            {"company": "ООО Ошибка", "contact_name": "Ошибочный", "email": "error@example.com"},
            {"company": "ООО Ок", "contact_name": "Ок", "email": "ok@example.com"},
        ],
    )
    with session_scope() as session:
        row = session.get(Campaign, errored["id"])
        if row:
            row.status = "completed_with_errors"
            row.sent_count = 1
            row.error_count = 1
            row.total_count = 2
        recs = session.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == errored["id"])
        ).all()
        if recs:
            recs[0].send_status = "failed"
            recs[0].last_error = "SMTP rejected"
            if len(recs) > 1:
                recs[1].send_status = "sent"
    _ensure_campaign_sent_mail_log(errored["id"])

    running = service.create_campaign(
        username,
        {
            "name": "Активная очередь демо",
            "mail_subject": "В процессе",
            "smtp_mailbox_id": mailpit_id,
            "email_template_id": email_tmpl["id"],
            "send_scenario": "materials_now",
        },
    )
    service.replace_recipients(
        running["id"],
        username,
        [
            {"company": f"ООО Queue {i}", "contact_name": f"Q{i}", "email": f"queue{i}@example.com"}
            for i in range(1, 6)
        ],
    )
    service.upsert_schedule(
        running["id"],
        username,
        {
            "send_immediately": True,
            "batch_size": 2,
            "interval_seconds": 120,
            "max_per_hour": 10,
            "max_per_day": 100,
        },
    )
    # Create visible pending batches without waiting for worker claim timing
    with session_scope() as session:
        row = session.get(Campaign, running["id"])
        recipients = session.scalars(
            select(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == running["id"])
            .order_by(CampaignRecipient.row_index)
        ).all()
        ids = [r.id for r in recipients]
        if row:
            row.status = "running"
            row.total_count = len(ids)
            row.sent_count = 0
            row.launched_at = datetime.now(timezone.utc)
        now = datetime.now(timezone.utc)
        for idx, start in enumerate(range(0, len(ids), 2)):
            chunk = ids[start : start + 2]
            session.add(
                CampaignBatch(
                    id=str(uuid.uuid4()),
                    campaign_id=running["id"],
                    batch_index=idx,
                    scheduled_at=now + timedelta(minutes=idx * 2),
                    size=len(chunk),
                    status="pending",
                    recipient_ids=chunk,
                )
            )

    backfilled = _backfill_sent_mail_logs_for_user(username)
    result = {
        "username": username,
        "mailpit_mailbox_id": mailpit_id,
        "templates": {
            "email": email_tmpl["id"],
            "kp": kp_tmpl["id"],
            "contract": contract_tmpl["id"],
        },
        "audiences": [audience["id"], audience2["id"]],
        "campaigns": {
            "draft": draft["id"],
            "scheduled": scheduled["id"],
            "completed": completed["id"],
            "errored": errored["id"],
            "running": running["id"],
        },
        "sent_mail_log_backfilled": backfilled,
    }
    logger.info("seed_demo_data_completed", username=username, sent_mail_log_backfilled=backfilled)
    return result


def seed_demo_data(*, force: bool = False) -> dict[str, Any]:
    ensure_smtp_key()
    demo_user = ensure_demo_user()
    results = {"demo": _seed_for_user(demo_user, force=force), "password": DEMO_PASSWORD}
    admin_username = str(getattr(settings, "app_username", "") or "").strip()
    if admin_username and get_user_record(admin_username) is not None:
        results["admin"] = _seed_for_user(admin_username, force=force)
    return results


if __name__ == "__main__":
    print(seed_demo_data(force=True))
