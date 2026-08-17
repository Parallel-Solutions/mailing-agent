"""Gradual sender warmup attached to a saved delivery connection."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from email.parser import Parser
import re
import random
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dns.resolver
import dns.reversename
from sqlalchemy import func, select

from src.infra.db import session_scope
from src.infra.models import (
    Campaign,
    ConnectionWarmupDelivery,
    ConnectionWarmupProgram,
    ConnectionWarmupRecipient,
    SmtpMailbox,
    SmtpOpenTracking,
)
from src.security.company_access import can_access_owner


def _build_daily_plan(max_growth_percent: int) -> list[int]:
    growth = min(30, max(20, int(max_growth_percent)))
    plan = [5, 8, 10, 15]
    while len(plan) < 7:
        next_value = (plan[-1] * (100 + growth) + 99) // 100
        plan.append(min(25, max(plan[-1] + 1, next_value)))
    plan.append(30)
    while len(plan) < 14:
        next_value = (plan[-1] * (100 + growth) + 99) // 100
        plan.append(min(50, max(plan[-1] + 1, next_value)))
    return plan


DEFAULT_DAILY_PLAN = _build_daily_plan(25)
FIXED_DAILY_MODE = "fixed_daily"
GROWTH_MODE = "growth"
MAX_WARMUP_DURATION_DAYS = 365
MAX_RECIPIENT_MESSAGES_PER_DAY = 100_000
CAMPAIGN_RESUME_POLL_SECONDS = 300
DEFAULT_SUBJECT_TEMPLATES = [
    "Короткое письмо",
    "Добрый день",
    "Проверка связи",
    "Небольшой вопрос",
    "На связи",
]
DEFAULT_BODY_TEMPLATES = [
    "Добрый день! Проверяю, что моя почта работает корректно. Хорошего дня!",
    "Здравствуйте! Это короткое индивидуальное письмо для проверки связи.",
    "Добрый день! Отправляю небольшое письмо без вложений и ссылок.",
    "Здравствуйте! Проверяю доставку обычного текстового письма.",
    "Добрый день! Небольшая проверка почтовой связи. Спасибо!",
]
ACTIVE_PROGRAM_STATUSES = {"running", "paused"}
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class WarmupSuspendedByCampaign(RuntimeError):
    def __init__(self, campaigns: list[dict[str, str]]):
        self.campaigns = campaigns
        super().__init__("RuSender warmup is suspended while a campaign is running.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _normalize_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 320 or not _EMAIL_RE.fullmatch(email):
        raise ValueError(f"Некорректный email получателя: {value}")
    return email


def _normalize_messages_per_day(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Количество писем в день должно быть целым числом.") from exc
    if result < 1 or result > MAX_RECIPIENT_MESSAGES_PER_DAY:
        raise ValueError(
            f"Количество писем на адрес должно быть от 1 до {MAX_RECIPIENT_MESSAGES_PER_DAY} в день."
        )
    return result


def _template_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} должны быть переданы списком.")
    templates = list(dict.fromkeys(str(item or "").strip() for item in value if str(item or "").strip()))
    if len(templates) < 3:
        raise ValueError(f"Добавьте минимум три разных варианта: {label.lower()}.")
    if len(templates) > 20:
        raise ValueError(f"Можно сохранить не более 20 вариантов: {label.lower()}.")
    return templates

def _provider_for_email(email: str) -> str:
    domain = email.rsplit("@", 1)[1]
    if domain in {"gmail.com", "googlemail.com"}:
        return "gmail"
    if domain in {"yandex.ru", "ya.ru", "yandex.com", "yandex.kz", "yandex.by"}:
        return "yandex"
    if domain in {"mail.ru", "inbox.ru", "bk.ru", "list.ru", "internet.ru"}:
        return "mailru"
    if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com"}:
        return "outlook"
    return "other"


def _get_connection(
    session: Any,
    connection_id: str,
    *,
    visible_owners: frozenset[str] | None,
) -> SmtpMailbox:
    row = session.get(SmtpMailbox, connection_id)
    if row is None or not can_access_owner(visible_owners, row.owner_username):
        raise LookupError("Подключение не найдено.")
    return row


def _validate_legacy_smtp_connection(
    target: SmtpMailbox,
    sender: SmtpMailbox,
    *,
    require_active: bool,
) -> None:
    from src.campaigns.connection_service import connection_transport
    if sender.owner_username != target.owner_username:
        raise ValueError("SMTP connection must belong to the same account.")

    if connection_transport(sender) != "smtp":
        raise ValueError("Для прогрева выберите SMTP-подключение.")
    if sender.email.strip().lower() != target.email.strip().lower():
        raise ValueError("SMTP-подключение должно использовать тот же email, который прогревается.")
    if require_active and sender.status != "active":
        raise ValueError("Сначала восстановите выбранное SMTP-подключение.")


def _validate_smtp_connection(
    target: SmtpMailbox,
    sender: SmtpMailbox,
    *,
    require_active: bool,
) -> None:
    """Validate the transport that actually owns the reputation being warmed."""
    from src.campaigns.connection_service import connection_transport

    target_transport = connection_transport(target)
    sender_transport = connection_transport(sender)
    if target_transport != "rusender":
        _validate_legacy_smtp_connection(
            target,
            sender,
            require_active=require_active,
        )
        return

    if sender.owner_username != target.owner_username:
        raise ValueError("RuSender connection must belong to the same account.")
    if sender_transport != "rusender":
        raise ValueError("Choose a RuSender connection to warm up a RuSender key.")
    if (
        sender.sending_key_id is None
        or target.sending_key_id is None
        or int(sender.sending_key_id) != int(target.sending_key_id)
    ):
        raise ValueError("The selected connection must use the same RuSender sending key.")
    if require_active and sender.status != "active":
        raise ValueError("Restore the selected RuSender connection before warmup.")


def _default_smtp_connection_id(connection: SmtpMailbox) -> str | None:
    from src.campaigns.connection_service import connection_transport

    return connection.id if connection_transport(connection) in {"smtp", "rusender"} else None


def _selected_smtp_connection(
    session: Any,
    program: ConnectionWarmupProgram,
) -> SmtpMailbox | None:
    if not program.smtp_connection_id:
        return None
    connection = session.get(SmtpMailbox, program.smtp_connection_id)
    if connection is None or connection.owner_username != program.owner_username:
        return None
    return connection


def _connection_transport(connection: SmtpMailbox | None) -> str:
    if connection is None:
        return "smtp"
    from src.campaigns.connection_service import connection_transport

    return connection_transport(connection)


def _active_rusender_campaigns(session: Any, connection: SmtpMailbox | None) -> list[dict[str, str]]:
    if connection is None or _connection_transport(connection) != "rusender":
        return []
    from src.generator.delivery.channel_guard import _campaign_uses_connection, _key_scope_mailboxes

    connection_ids = {row.id for row in _key_scope_mailboxes(session, connection)}
    campaigns = session.scalars(
        select(Campaign).where(
            Campaign.owner_username == connection.owner_username,
            Campaign.status == "running",
        )
    ).all()
    return [
        {"id": campaign.id, "name": campaign.name or campaign.id}
        for campaign in campaigns
        if any(_campaign_uses_connection(campaign, connection_id) for connection_id in connection_ids)
    ]


def active_rusender_campaigns_for_connection(connection_id: str) -> list[dict[str, str]]:
    with session_scope() as session:
        connection = session.get(SmtpMailbox, connection_id)
        return _active_rusender_campaigns(session, connection)


def _fixed_daily_plan(
    program: ConnectionWarmupProgram,
    recipients: list[ConnectionWarmupRecipient],
) -> list[int]:
    total = sum(
        max(1, int(recipient.messages_per_day or 1))
        for recipient in recipients
        if recipient.status == "active"
    )
    duration = max(1, min(MAX_WARMUP_DURATION_DAYS, int(program.duration_days or 14)))
    return [total for _ in range(duration)]


def _ensure_program_locked(session: Any, connection: SmtpMailbox) -> ConnectionWarmupProgram:
    program = session.scalar(
        select(ConnectionWarmupProgram)
        .where(ConnectionWarmupProgram.connection_id == connection.id)
        .with_for_update()
    )
    if program is not None:
        if program.smtp_connection_id is None:
            program.smtp_connection_id = _default_smtp_connection_id(connection)
            program.updated_at = _now()
        if (
            _connection_transport(connection) == "rusender"
            and str(program.warmup_mode or GROWTH_MODE) != FIXED_DAILY_MODE
            and program.status not in ACTIVE_PROGRAM_STATUSES
        ):
            program.warmup_mode = FIXED_DAILY_MODE
            program.duration_days = max(1, int(program.duration_days or 14))
            program.updated_at = _now()
        return program
    now = _now()
    program = ConnectionWarmupProgram(
        id=str(uuid4()),
        connection_id=connection.id,
        smtp_connection_id=_default_smtp_connection_id(connection),
        owner_username=connection.owner_username,
        status="draft",
        timezone="Europe/Moscow",
        daily_start_time="10:00",
        daily_end_time="18:00",
        pause_campaigns_during_warmup=True,
        warmup_mode=(FIXED_DAILY_MODE if _connection_transport(connection) == "rusender" else GROWTH_MODE),
        duration_days=14,
        max_growth_percent=25,
        current_day=1,
        run_number=1,
        daily_plan=list(DEFAULT_DAILY_PLAN),
        diagnostics_status="not_checked",
        diagnostics={},
        subject_templates=list(DEFAULT_SUBJECT_TEMPLATES),
        body_templates=list(DEFAULT_BODY_TEMPLATES),
        pause_reason=None,
        suspended_by_campaign=False,
        suspended_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(program)
    session.flush()
    return program


def _recipient_dict(row: ConnectionWarmupRecipient) -> dict[str, Any]:
    return {
        "id": row.id,
        "email": row.email,
        "provider": row.provider,
        "status": row.status,
        "messages_per_day": max(1, int(row.messages_per_day or 1)),
        "sent_count": int(row.sent_count or 0),
        "error_count": int(row.error_count or 0),
        "last_sent_at": _iso(row.last_sent_at),
        "last_error": row.last_error or "",
        "created_at": _iso(row.created_at),
    }


def _program_dict(session: Any, program: ConnectionWarmupProgram) -> dict[str, Any]:
    recipients = session.execute(
        select(ConnectionWarmupRecipient)
        .where(
            ConnectionWarmupRecipient.program_id == program.id,
            ConnectionWarmupRecipient.status != "removed",
        )
        .order_by(ConnectionWarmupRecipient.created_at.asc())
    ).scalars().all()
    delivery_counts = dict(
        session.execute(
            select(ConnectionWarmupDelivery.status, func.count())
            .where(
                ConnectionWarmupDelivery.program_id == program.id,
                ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
            )
            .group_by(ConnectionWarmupDelivery.status)
        ).all()
    )
    smtp_opened_count = int(
        session.scalar(
            select(func.count(SmtpOpenTracking.id))
            .join(
                ConnectionWarmupDelivery,
                ConnectionWarmupDelivery.id == SmtpOpenTracking.warmup_delivery_id,
            )
            .where(
                ConnectionWarmupDelivery.program_id == program.id,
                ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
                SmtpOpenTracking.first_opened_at.is_not(None),
            )
        )
        or 0
    )
    provider_opened_count = int(
        session.scalar(
            select(func.count(ConnectionWarmupDelivery.id)).where(
                ConnectionWarmupDelivery.program_id == program.id,
                ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
                ConnectionWarmupDelivery.opened_at.is_not(None),
            )
        )
        or 0
    )
    delivery_counts["opened"] = smtp_opened_count + provider_opened_count
    active_count = sum(1 for item in recipients if item.status == "active")
    smtp_connection = _selected_smtp_connection(session, program)
    target_connection = session.get(SmtpMailbox, program.connection_id)
    sending_connection = smtp_connection or target_connection
    warmup_mode = str(program.warmup_mode or GROWTH_MODE)
    plan = (
        _fixed_daily_plan(program, recipients)
        if warmup_mode == FIXED_DAILY_MODE
        else [max(1, int(value)) for value in list(program.daily_plan or DEFAULT_DAILY_PLAN)]
    )
    effective_plan = list(plan) if active_count else [0 for _ in plan]
    active_campaigns = _active_rusender_campaigns(session, sending_connection)
    suspended_by_campaign = bool(program.suspended_by_campaign or active_campaigns)
    pause_reason = program.pause_reason or (
        "Прогрев временно остановлен: выполняется рассылка."
        if active_campaigns else ""
    )
    return {
        "id": program.id,
        "connection_id": program.connection_id,
        "smtp_connection_id": program.smtp_connection_id or "",
        "smtp_connection_email": smtp_connection.email if smtp_connection else "",
        "smtp_connection_status": smtp_connection.status if smtp_connection else "not_selected",
        "sending_transport": _connection_transport(sending_connection),
        "status": program.status,
        "timezone": program.timezone,
        "daily_start_time": program.daily_start_time,
        "daily_end_time": program.daily_end_time,
        "pause_campaigns_during_warmup": bool(program.pause_campaigns_during_warmup),
        "warmup_mode": warmup_mode,
        "duration_days": max(1, int(program.duration_days or len(plan) or 14)),
        "max_growth_percent": int(program.max_growth_percent or 25),
        "current_day": int(program.current_day or 1),
        "run_number": int(program.run_number or 1),
        "daily_plan": plan,
        "effective_daily_plan": effective_plan,
        "diagnostics_status": program.diagnostics_status,
        "diagnostics": dict(program.diagnostics or {}),
        "subject_templates": list(program.subject_templates or DEFAULT_SUBJECT_TEMPLATES),
        "body_templates": list(program.body_templates or DEFAULT_BODY_TEMPLATES),
        "pause_reason": pause_reason,
        "suspended_by_campaign": suspended_by_campaign,
        "suspended_at": _iso(program.suspended_at),
        "active_campaigns": active_campaigns,
        "recipients_consent_confirmed": bool(program.recipients_consent_confirmed),
        "recipients_consent_confirmed_at": _iso(program.recipients_consent_confirmed_at),
        "scheduled_task_id": program.scheduled_task_id or "",
        "recipients": [_recipient_dict(item) for item in recipients],
        "recipient_count": len(recipients),
        "active_recipient_count": active_count,
        "delivery_counts": {str(key): int(value) for key, value in delivery_counts.items()},
        "started_at": _iso(program.started_at),
        "paused_at": _iso(program.paused_at),
        "completed_at": _iso(program.completed_at),
        "created_at": _iso(program.created_at),
        "updated_at": _iso(program.updated_at),
    }


def get_program(
    connection_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        return _program_dict(session, program)


def update_program(
    connection_id: str,
    owner_username: str,
    data: dict[str, Any],
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        if program.status == "running":
            raise ValueError("Сначала поставьте прогрев на паузу.")
        if "smtp_connection_id" in data:
            requested_id = str(data.get("smtp_connection_id") or "").strip()
            smtp_connection_id = requested_id or _default_smtp_connection_id(connection)
            if smtp_connection_id:
                smtp_connection = _get_connection(
                    session,
                    smtp_connection_id,
                    visible_owners=visible_owners,
                )
                _validate_smtp_connection(connection, smtp_connection, require_active=False)
            if smtp_connection_id != program.smtp_connection_id:
                program.smtp_connection_id = smtp_connection_id
                program.diagnostics_status = "not_checked"
                program.diagnostics = {}
        if "timezone" in data:
            timezone_name = str(data.get("timezone") or "").strip()
            try:
                ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("Неизвестный часовой пояс.") from exc
            program.timezone = timezone_name
        if "daily_start_time" in data:
            daily_start_time = str(data.get("daily_start_time") or "").strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_start_time):
                raise ValueError("Время начала должно быть в формате HH:MM.")
            program.daily_start_time = daily_start_time
        if "daily_end_time" in data:
            daily_end_time = str(data.get("daily_end_time") or "").strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_end_time):
                raise ValueError("Время окончания должно быть в формате HH:MM.")
            program.daily_end_time = daily_end_time
        if "daily_start_time" in data or "daily_end_time" in data:
            if program.daily_end_time <= program.daily_start_time:
                raise ValueError("Время окончания должно быть позже времени начала.")
        if "pause_campaigns_during_warmup" in data:
            program.pause_campaigns_during_warmup = bool(data.get("pause_campaigns_during_warmup"))
        if "duration_days" in data:
            duration_days = int(data.get("duration_days") or 0)
            if duration_days < 1 or duration_days > MAX_WARMUP_DURATION_DAYS:
                raise ValueError(
                    f"Количество дней прогрева должно быть от 1 до {MAX_WARMUP_DURATION_DAYS}."
                )
            program.duration_days = duration_days
            if _connection_transport(connection) == "rusender":
                program.warmup_mode = FIXED_DAILY_MODE
        if "subject_templates" in data:
            program.subject_templates = _template_list(data.get("subject_templates"), label="Темы")
        if "body_templates" in data:
            program.body_templates = _template_list(data.get("body_templates"), label="Тексты")
        if "max_growth_percent" in data:
            growth = int(data.get("max_growth_percent") or 0)
            if growth < 20 or growth > 30:
                raise ValueError("Рост должен быть от 20% до 30% в день.")
            program.max_growth_percent = growth
            program.daily_plan = _build_daily_plan(growth)
        if "recipients_consent_confirmed" in data:
            confirmed = bool(data.get("recipients_consent_confirmed"))
            program.recipients_consent_confirmed = confirmed
            program.recipients_consent_confirmed_at = _now() if confirmed else None
        program.updated_at = _now()
        session.flush()
        return _program_dict(session, program)


def add_recipients(
    connection_id: str,
    owner_username: str,
    emails: list[str],
    *,
    recipient_settings: list[dict[str, Any]] | None = None,
    messages_per_day: int = 1,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    default_messages_per_day = _normalize_messages_per_day(messages_per_day)
    specifications: dict[str, int] = {}
    for value in emails:
        specifications[_normalize_email(value)] = default_messages_per_day
    for item in list(recipient_settings or []):
        if not isinstance(item, dict):
            raise ValueError("Настройки получателей должны быть переданы объектами.")
        email = _normalize_email(item.get("email"))
        specifications[email] = _normalize_messages_per_day(
            item.get("messages_per_day", default_messages_per_day)
        )
    normalized = list(specifications)
    if not normalized:
        raise ValueError("Добавьте хотя бы один адрес.")
    if len(normalized) > 500:
        raise ValueError("За один запрос можно добавить не более 500 адресов.")
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        if program.status == "running":
            raise ValueError("Сначала поставьте прогрев на паузу.")
        existing = {
            row.email: row
            for row in session.execute(
                select(ConnectionWarmupRecipient).where(
                    ConnectionWarmupRecipient.program_id == program.id,
                    ConnectionWarmupRecipient.email.in_(normalized),
                )
            ).scalars()
        }
        now = _now()
        for email in normalized:
            row = existing.get(email)
            if row is not None:
                row.status = "active"
                row.messages_per_day = specifications[email]
                row.updated_at = now
                continue
            session.add(
                ConnectionWarmupRecipient(
                    id=str(uuid4()),
                    program_id=program.id,
                    email=email,
                    provider=_provider_for_email(email),
                    status="active",
                    messages_per_day=specifications[email],
                    sent_count=0,
                    error_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        program.updated_at = now
        if _connection_transport(connection) == "rusender":
            program.warmup_mode = FIXED_DAILY_MODE
        session.flush()
        return _program_dict(session, program)


def set_recipient_status(
    connection_id: str,
    recipient_id: str,
    owner_username: str,
    status: str | None,
    *,
    messages_per_day: int | None = None,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    if status is None and messages_per_day is None:
        raise ValueError("Укажите статус или количество писем в день.")
    if status is not None and status not in {"active", "disabled", "removed"}:
        raise ValueError("Некорректный статус получателя.")
    normalized_messages_per_day = (
        _normalize_messages_per_day(messages_per_day)
        if messages_per_day is not None
        else None
    )
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        recipient = session.get(ConnectionWarmupRecipient, recipient_id)
        if recipient is None or recipient.program_id != program.id:
            raise LookupError("Получатель прогрева не найден.")
        if normalized_messages_per_day is not None:
            if program.status == "running":
                raise ValueError("Сначала поставьте прогрев на паузу.")
            recipient.messages_per_day = normalized_messages_per_day
            if _connection_transport(connection) == "rusender":
                program.warmup_mode = FIXED_DAILY_MODE
        if status is not None:
            recipient.status = status
        recipient.updated_at = _now()
        program.updated_at = _now()
        session.flush()
        return _program_dict(session, program)


def _txt_records(name: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=5)
    except Exception:
        return []
    return ["".join(part.decode("utf-8", errors="replace") for part in answer.strings) for answer in answers]


def _ptr_records(ip_address: str) -> list[str]:
    try:
        reverse_name = dns.reversename.from_address(ip_address)
        answers = dns.resolver.resolve(reverse_name, "PTR", lifetime=5)
    except Exception:
        return []
    return [str(answer).rstrip(".") for answer in answers]

def _aligned(from_domain: str, auth_domain: str) -> bool:
    left = from_domain.strip(".").lower()
    right = auth_domain.strip(".").lower()
    return bool(left and right and (left == right or left.endswith(f".{right}") or right.endswith(f".{left}")))


def run_diagnostics(
    connection_id: str,
    owner_username: str,
    *,
    headers: str = "",
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        subject_templates = list(program.subject_templates or DEFAULT_SUBJECT_TEMPLATES)
        body_templates = list(program.body_templates or DEFAULT_BODY_TEMPLATES)
        smtp_connection = _selected_smtp_connection(session, program)
        diagnostic_connection = smtp_connection or connection
        from_domain = diagnostic_connection.email.rsplit("@", 1)[-1].lower()
        try:
            if smtp_connection is None:
                raise ValueError("Выберите SMTP-подключение для прогрева.")
            _validate_smtp_connection(connection, smtp_connection, require_active=True)
            smtp_check = {
                "key": "smtp_connection",
                "status": "pass",
                "detail": f"SMTP: {smtp_connection.email} ({smtp_connection.host}:{smtp_connection.port}).",
            }
        except ValueError as exc:
            smtp_check = {
                "key": "smtp_connection",
                "status": "fail",
                "detail": str(exc),
            }

    dmarc_records = [value for value in _txt_records(f"_dmarc.{from_domain}") if value.lower().startswith("v=dmarc1")]
    parsed = Parser().parsestr(headers) if headers.strip() else None
    authentication = " ".join(parsed.get_all("Authentication-Results", [])) if parsed else ""
    dkim_signature = " ".join(parsed.get_all("DKIM-Signature", [])) if parsed else ""
    return_path = str(parsed.get("Return-Path") or "") if parsed else ""
    received = " ".join(parsed.get_all("Received", [])) if parsed else ""
    dkim_domain_match = re.search(r"(?:^|;)\s*d=([^;\s]+)", dkim_signature, flags=re.IGNORECASE)
    dkim_domain = dkim_domain_match.group(1).strip() if dkim_domain_match else ""
    return_domain_match = re.search(r"@([^>\s]+)", return_path)
    return_domain = return_domain_match.group(1).strip() if return_domain_match else ""
    spf_domain = return_domain or from_domain
    spf_records = [value for value in _txt_records(spf_domain) if value.lower().startswith("v=spf1")]
    dkim_selector_match = re.search(r"(?:^|;)\s*s=([^;\s]+)", dkim_signature, flags=re.IGNORECASE)
    dkim_selector = dkim_selector_match.group(1).strip() if dkim_selector_match else ""
    dkim_records = [
        value
        for value in _txt_records(f"{dkim_selector}._domainkey.{dkim_domain}")
        if value.lower().startswith("v=dkim1")
    ] if dkim_selector and dkim_domain else []
    outbound_ips = list(dict.fromkeys(_IP_RE.findall(received)))
    ptr_records = {ip: _ptr_records(ip) for ip in outbound_ips}
    combined_content = "\n".join([*subject_templates, *body_templates])
    urls = re.findall(r"https?://[^\s<>]+", combined_content, flags=re.IGNORECASE)
    shortener_domains = ("bit.ly", "t.co", "tinyurl.com", "clck.ru", "goo.gl")
    short_urls = [url for url in urls if any(domain in url.lower() for domain in shortener_domains)]
    auth_lower = authentication.lower()

    checks: list[dict[str, str]] = []
    checks.append(smtp_check)
    checks.append({
        "key": "spf_record",
        "status": "pass" if spf_records else "fail",
        "detail": spf_records[0] if spf_records else "SPF-запись не найдена.",
    })
    checks.append({
        "key": "dmarc_record",
        "status": "pass" if dmarc_records else "fail",
        "detail": dmarc_records[0] if dmarc_records else "DMARC-запись не найдена.",
    })
    if parsed:
        spf_pass = "spf=pass" in auth_lower
        dkim_pass = "dkim=pass" in auth_lower
        checks.extend([
            {
                "key": "dkim_record",
                "status": "pass" if dkim_records else "fail",
                "detail": dkim_records[0] if dkim_records else "Публичный DKIM-ключ из заголовков не найден в DNS.",
            },
            {"key": "spf_result", "status": "pass" if spf_pass else "fail", "detail": "SPF pass" if spf_pass else "В заголовках нет SPF pass."},
            {"key": "dkim_result", "status": "pass" if dkim_pass else "fail", "detail": "DKIM pass" if dkim_pass else "В заголовках нет DKIM pass."},
            {
                "key": "alignment",
                "status": "pass" if (_aligned(from_domain, dkim_domain) or _aligned(from_domain, return_domain)) else "fail",
                "detail": f"From={from_domain}; DKIM={dkim_domain or '—'}; Return-Path={return_domain or '—'}",
            },
        ])
    else:
        checks.append({"key": "sample_headers", "status": "warning", "detail": "Добавьте заголовки тестового письма для проверки DKIM, alignment и IP."})
    if outbound_ips:
        missing_ptr = [ip for ip, values in ptr_records.items() if not values]
        checks.append({
            "key": "ptr",
            "status": "warning" if missing_ptr else "pass",
            "detail": f"PTR не найден: {', '.join(missing_ptr)}" if missing_ptr else "; ".join(f"{ip} → {', '.join(values)}" for ip, values in ptr_records.items()),
        })
    checks.append({
        "key": "template_variation",
        "status": "pass" if len(set(subject_templates)) >= 3 and len(set(body_templates)) >= 3 else "fail",
        "detail": f"Тем: {len(set(subject_templates))}; текстов: {len(set(body_templates))}.",
    })
    checks.append({
        "key": "content_links",
        "status": "warning" if urls else "pass",
        "detail": f"Найдено ссылок: {len(urls)}." if urls else "Ссылки не найдены.",
    })
    checks.append({
        "key": "short_links",
        "status": "fail" if short_urls else "pass",
        "detail": f"Найдены сокращённые ссылки: {', '.join(short_urls)}" if short_urls else "Сокращатели ссылок не найдены.",
    })
    checks.append({
        "key": "reputation",
        "status": "warning",
        "detail": "Репутация и blacklist требуют подключения внешнего источника данных.",
    })

    failed = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warning"]
    status = "blocked" if failed else "warning" if warnings else "ready"
    report = {
        "checked_at": _iso(_now()),
        "from_domain": from_domain,
        "status": status,
        "checks": checks,
        "spf_domain": spf_domain,
        "spf_records": spf_records,
        "dmarc_records": dmarc_records,
        "dkim_domain": dkim_domain,
        "dkim_selector": dkim_selector,
        "dkim_records": dkim_records,
        "return_path_domain": return_domain,
        "outbound_ips": outbound_ips,
        "ptr_records": ptr_records,
        "urls": urls,
        "short_urls": short_urls,
    }
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        program.diagnostics_status = status
        program.diagnostics = report
        program.updated_at = _now()
        session.flush()
        return _program_dict(session, program)


def _day_task_available_at(program: ConnectionWarmupProgram, *, immediate: bool) -> datetime:
    if immediate:
        return _now()
    hour, minute = [int(value) for value in program.daily_start_time.split(":", 1)]
    local_now = _now().astimezone(ZoneInfo(program.timezone))
    next_local = (local_now + timedelta(days=1)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    return next_local.astimezone(timezone.utc)


def _message_schedule(
    program: ConnectionWarmupProgram,
    count: int,
) -> list[datetime]:
    if count <= 0:
        return []
    zone = ZoneInfo(program.timezone)
    local_now = _now().astimezone(zone)
    start_hour, start_minute = [int(value) for value in program.daily_start_time.split(":", 1)]
    end_hour, end_minute = [int(value) for value in program.daily_end_time.split(":", 1)]
    start = local_now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = local_now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if local_now >= end:
        start += timedelta(days=1)
        end += timedelta(days=1)
    elif local_now > start:
        start = local_now + timedelta(minutes=1)
    span_seconds = max(60, int((end - start).total_seconds()))
    spacing = span_seconds / (count + 1)
    jitter_limit = min(300.0, spacing * 0.15)
    randomizer = random.Random(f"{program.id}:{program.run_number}:{program.current_day}")
    return [
        (
            start + timedelta(seconds=spacing * (index + 1) + randomizer.uniform(-jitter_limit, jitter_limit))
        ).astimezone(timezone.utc)
        for index in range(count)
    ]


def _enqueue_day(program_id: str, *, immediate: bool) -> str:
    from src.workers.task_queue import enqueue_task

    with session_scope() as session:
        program = session.get(ConnectionWarmupProgram, program_id)
        if program is None:
            raise LookupError("Программа прогрева не найдена.")
        available_at = _day_task_available_at(program, immediate=immediate)
        owner_username = program.owner_username
        day_number = int(program.current_day or 1)
        run_number = int(program.run_number or 1)
    task, _ = enqueue_task(
        task_type="connection_sender_warmup",
        job_id=program_id,
        owner_username=owner_username,
        payload={"program_id": program_id, "run_number": run_number},
        max_attempts=2,
        available_at=available_at,
        idempotency_key=f"connection_sender_warmup:{program_id}:{run_number}:{day_number}:{uuid4()}",
        active_key=f"connection_sender_warmup:{program_id}:{run_number}:{day_number}",
    )
    task_id = str(task["id"])
    with session_scope() as session:
        program = session.get(ConnectionWarmupProgram, program_id)
        if program is not None:
            program.scheduled_task_id = task_id
            program.updated_at = _now()
    return task_id


def _enqueue_delivery(delivery_id: str, *, available_at: datetime) -> str:
    from src.workers.task_queue import enqueue_task

    with session_scope() as session:
        delivery = session.get(ConnectionWarmupDelivery, delivery_id)
        if delivery is None:
            raise LookupError("Письмо прогрева не найдено.")
        program = session.get(ConnectionWarmupProgram, delivery.program_id)
        if program is None:
            raise LookupError("Программа прогрева не найдена.")
        owner_username = program.owner_username
    task, _ = enqueue_task(
        task_type="connection_sender_warmup_message",
        job_id=delivery_id,
        owner_username=owner_username,
        payload={"delivery_id": delivery_id},
        max_attempts=2,
        available_at=available_at,
        idempotency_key=f"connection_sender_warmup_message:{delivery_id}:{uuid4()}",
        active_key=f"connection_sender_warmup_message:{delivery_id}",
    )
    task_id = str(task["id"])
    with session_scope() as session:
        delivery = session.get(ConnectionWarmupDelivery, delivery_id)
        if delivery is not None:
            delivery.task_id = task_id
            delivery.status = "queued"
            delivery.scheduled_at = available_at
            delivery.updated_at = _now()
    return task_id


def _enqueue_campaign_resume(program_id: str) -> str:
    """Schedule one deduplicated poll for a RuSender key occupied by a campaign."""
    from src.workers.task_queue import enqueue_task

    with session_scope() as session:
        program = session.get(ConnectionWarmupProgram, program_id)
        if program is None:
            raise LookupError("Программа прогрева не найдена.")
        owner_username = program.owner_username
        run_number = int(program.run_number or 1)
        day_number = int(program.current_day or 1)
    now = _now()
    slot = int(now.timestamp() // CAMPAIGN_RESUME_POLL_SECONDS) + 1
    available_at = datetime.fromtimestamp(
        slot * CAMPAIGN_RESUME_POLL_SECONDS,
        tz=timezone.utc,
    )
    poll_key = f"connection_sender_warmup_campaign_poll:{program_id}:{run_number}:{day_number}:{slot}"
    task, _ = enqueue_task(
        task_type="connection_sender_warmup",
        job_id=program_id,
        owner_username=owner_username,
        payload={"program_id": program_id, "run_number": run_number, "campaign_poll": True},
        max_attempts=2,
        available_at=available_at,
        idempotency_key=poll_key,
        active_key=poll_key,
    )
    task_id = str(task["id"])
    with session_scope() as session:
        program = session.get(ConnectionWarmupProgram, program_id)
        if program is not None:
            program.scheduled_task_id = task_id
            program.updated_at = _now()
    return task_id


def _set_campaign_suspension(
    program: ConnectionWarmupProgram,
    campaigns: list[dict[str, str]],
) -> None:
    names = ", ".join(item["name"] for item in campaigns[:3])
    suffix = f": {names}" if names else ""
    program.suspended_by_campaign = True
    program.suspended_at = program.suspended_at or _now()
    program.pause_reason = f"Прогрев временно остановлен: выполняется рассылка{suffix}."
    program.updated_at = _now()


def _clear_campaign_suspension(program: ConnectionWarmupProgram) -> None:
    if not program.suspended_by_campaign:
        return
    program.suspended_by_campaign = False
    program.suspended_at = None
    program.pause_reason = None
    program.updated_at = _now()


def _assert_no_other_key_warmup(
    session: Any,
    connection: SmtpMailbox,
    program: ConnectionWarmupProgram,
) -> None:
    if _connection_transport(connection) != "rusender":
        return
    from src.generator.delivery.channel_guard import _ensure_key_guard, _key_scope_mailboxes

    _ensure_key_guard(session, connection, for_update=True)
    connection_ids = [row.id for row in _key_scope_mailboxes(session, connection)]
    conflict = session.scalar(
        select(ConnectionWarmupProgram)
        .where(
            ConnectionWarmupProgram.id != program.id,
            ConnectionWarmupProgram.connection_id.in_(connection_ids),
            ConnectionWarmupProgram.status.in_(ACTIVE_PROGRAM_STATUSES),
        )
        .limit(1)
    )
    if conflict is not None:
        raise ValueError("Для этого ключа RuSender уже запущена или приостановлена другая программа прогрева.")


def _cancel_task_ids(task_ids: list[str]) -> None:
    from src.workers.task_queue import request_cancel

    for task_id in dict.fromkeys(value for value in task_ids if value):
        try:
            request_cancel(task_id)
        except Exception:
            continue


def _pause_locked(
    session: Any,
    program: ConnectionWarmupProgram,
    *,
    reason: str,
) -> list[str]:
    task_ids = [str(program.scheduled_task_id or "")]
    deliveries = session.execute(
        select(ConnectionWarmupDelivery).where(
            ConnectionWarmupDelivery.program_id == program.id,
            ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
            ConnectionWarmupDelivery.status.in_({"queued", "sending"}),
        )
    ).scalars().all()
    for delivery in deliveries:
        task_ids.append(str(delivery.task_id or ""))
        delivery.status = "unknown" if delivery.status == "sending" else "paused"
        delivery.task_id = None
        delivery.updated_at = _now()
    program.status = "paused"
    program.pause_reason = reason
    program.suspended_by_campaign = False
    program.suspended_at = None
    program.paused_at = _now()
    program.scheduled_task_id = None
    program.updated_at = _now()
    return task_ids


def start_program(
    connection_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    pause_campaigns = False
    wait_for_campaign = False
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        if connection.status != "active":
            raise ValueError("Сначала восстановите подключение.")
        if program.status == "running":
            return _program_dict(session, program)
        smtp_connection = _selected_smtp_connection(session, program)
        if smtp_connection is None:
            raise ValueError("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 SMTP-\u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u0434\u043b\u044f \u043f\u0440\u043e\u0433\u0440\u0435\u0432\u0430.")
        _validate_smtp_connection(connection, smtp_connection, require_active=True)
        _assert_no_other_key_warmup(session, smtp_connection, program)
        if program.diagnostics_status not in {"ready", "warning"}:
            raise ValueError("Сначала выполните техническую проверку и устраните критические ошибки.")
        active_count = session.scalar(
            select(func.count()).select_from(ConnectionWarmupRecipient).where(
                ConnectionWarmupRecipient.program_id == program.id,
                ConnectionWarmupRecipient.status == "active",
            )
        )
        if not program.recipients_consent_confirmed:
            raise ValueError("Подтвердите согласие владельцев адресов на получение писем.")
        if not active_count:
            raise ValueError("Добавьте хотя бы один активный адрес получателя.")
        _template_list(list(program.subject_templates or DEFAULT_SUBJECT_TEMPLATES), label="Темы")
        _template_list(list(program.body_templates or DEFAULT_BODY_TEMPLATES), label="Тексты")
        if program.status in {"completed", "cancelled"}:
            program.run_number = int(program.run_number or 1) + 1
            program.current_day = 1
            program.started_at = None
            program.completed_at = None
        program.status = "running"
        program.started_at = program.started_at or _now()
        program.paused_at = None
        program.pause_reason = None
        program.suspended_by_campaign = False
        program.suspended_at = None
        program.updated_at = _now()
        program_id = program.id
        active_campaigns = _active_rusender_campaigns(session, smtp_connection)
        if active_campaigns:
            _set_campaign_suspension(program, active_campaigns)
            wait_for_campaign = True
        pause_campaigns = (
            bool(program.pause_campaigns_during_warmup)
            and _connection_transport(smtp_connection) != "rusender"
        )
        smtp_connection_id = smtp_connection.id
    if pause_campaigns:
        from src.generator.delivery.channel_guard import _pause_campaigns_for_channel

        _pause_campaigns_for_channel(smtp_connection_id, reason="connection_sender_warmup")
    if wait_for_campaign:
        _enqueue_campaign_resume(program_id)
    else:
        _enqueue_day(program_id, immediate=True)
    return get_program(connection_id, "", visible_owners=visible_owners)


def pause_program(
    connection_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        if program.status != "running":
            raise ValueError("Прогрев сейчас не выполняется.")
        task_ids = _pause_locked(session, program, reason="Остановлено пользователем.")
    _cancel_task_ids(task_ids)
    return get_program(connection_id, "", visible_owners=visible_owners)


def resume_program(
    connection_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    wait_for_campaign = False
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        if program.status != "paused":
            raise ValueError("Программа прогрева не находится на паузе.")
        smtp_connection = _selected_smtp_connection(session, program)
        if smtp_connection is None:
            raise ValueError("Выберите SMTP-подключение для прогрева.")
        _validate_smtp_connection(connection, smtp_connection, require_active=True)
        _assert_no_other_key_warmup(session, smtp_connection, program)
        if program.diagnostics_status not in {"ready", "warning"}:
            raise ValueError("\u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u0442\u0435\u0445\u043d\u0438\u0447\u0435\u0441\u043a\u0443\u044e \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443 \u043f\u043e\u0441\u043b\u0435 \u0441\u043c\u0435\u043d\u044b SMTP-\u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u044f.")
        program.status = "running"
        program.paused_at = None
        program.pause_reason = None
        program.suspended_by_campaign = False
        program.suspended_at = None
        program.updated_at = _now()
        program_id = program.id
        active_campaigns = _active_rusender_campaigns(session, smtp_connection)
        if active_campaigns:
            _set_campaign_suspension(program, active_campaigns)
            wait_for_campaign = True
    if wait_for_campaign:
        _enqueue_campaign_resume(program_id)
    else:
        _enqueue_day(program_id, immediate=True)
    return get_program(connection_id, "", visible_owners=visible_owners)


def stop_program(
    connection_id: str,
    owner_username: str,
    *,
    visible_owners: frozenset[str] | None = None,
) -> dict[str, Any]:
    del owner_username
    with session_scope() as session:
        connection = _get_connection(session, connection_id, visible_owners=visible_owners)
        program = _ensure_program_locked(session, connection)
        task_ids = [str(program.scheduled_task_id or "")]
        deliveries = session.execute(
            select(ConnectionWarmupDelivery).where(
                ConnectionWarmupDelivery.program_id == program.id,
                ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
                ConnectionWarmupDelivery.status.in_({"queued", "paused", "sending"}),
            )
        ).scalars().all()
        for delivery in deliveries:
            task_ids.append(str(delivery.task_id or ""))
            delivery.status = "unknown" if delivery.status == "sending" else "cancelled"
            delivery.task_id = None
            delivery.updated_at = _now()
        program.status = "cancelled"
        program.pause_reason = "Остановлено пользователем."
        program.scheduled_task_id = None
        program.suspended_by_campaign = False
        program.suspended_at = None
        program.completed_at = _now()
        program.updated_at = _now()
    _cancel_task_ids(task_ids)
    return get_program(connection_id, "", visible_owners=visible_owners)


def run_warmup_day(kwargs: dict[str, Any]) -> dict[str, Any]:
    program_id = str(kwargs.get("program_id") or "").strip()
    if not program_id:
        raise ValueError("program_id is required")
    expected_run_number = int(kwargs.get("run_number") or 0)
    campaign_blocked = False
    blocking_campaigns: list[dict[str, str]] = []
    with session_scope() as session:
        program = session.execute(
            select(ConnectionWarmupProgram).where(ConnectionWarmupProgram.id == program_id).with_for_update()
        ).scalar_one_or_none()
        if program is None:
            raise LookupError("Программа прогрева не найдена.")
        if program.status != "running":
            return {"program_id": program_id, "status": program.status, "scheduled": 0}
        if expected_run_number and expected_run_number != int(program.run_number or 1):
            return {"program_id": program_id, "status": "stale", "scheduled": 0}
        sending_connection = _selected_smtp_connection(session, program)
        blocking_campaigns = _active_rusender_campaigns(session, sending_connection)
        if blocking_campaigns:
            _set_campaign_suspension(program, blocking_campaigns)
            program.scheduled_task_id = None
            campaign_blocked = True
        else:
            _clear_campaign_suspension(program)
    if campaign_blocked:
        _enqueue_campaign_resume(program_id)
        return {
            "program_id": program_id,
            "status": "suspended_by_campaign",
            "scheduled": 0,
            "campaigns": blocking_campaigns,
        }
    with session_scope() as session:
        program = session.execute(
            select(ConnectionWarmupProgram).where(ConnectionWarmupProgram.id == program_id).with_for_update()
        ).scalar_one_or_none()
        if program is None:
            raise LookupError("Программа прогрева не найдена.")
        if program.status != "running":
            return {"program_id": program_id, "status": program.status, "scheduled": 0}
        if expected_run_number and expected_run_number != int(program.run_number or 1):
            return {"program_id": program_id, "status": "stale", "scheduled": 0}
        day_number = int(program.current_day or 1)
        existing = session.execute(
            select(ConnectionWarmupDelivery)
            .where(
                ConnectionWarmupDelivery.program_id == program.id,
                ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
                ConnectionWarmupDelivery.day_number == day_number,
            )
            .order_by(ConnectionWarmupDelivery.sequence_number.asc())
        ).scalars().all()
        recipients = session.execute(
            select(ConnectionWarmupRecipient)
            .where(
                ConnectionWarmupRecipient.program_id == program.id,
                ConnectionWarmupRecipient.status == "active",
            )
            .order_by(
                ConnectionWarmupRecipient.sent_count.asc(),
                ConnectionWarmupRecipient.last_sent_at.asc().nullsfirst(),
                ConnectionWarmupRecipient.created_at.asc(),
            )
        ).scalars().all()
        warmup_mode = str(program.warmup_mode or GROWTH_MODE)
        plan = (
            _fixed_daily_plan(program, recipients)
            if warmup_mode == FIXED_DAILY_MODE
            else [max(1, int(value)) for value in list(program.daily_plan or DEFAULT_DAILY_PLAN)]
        )
        if day_number > len(plan):
            program.status = "completed"
            program.completed_at = _now()
            program.scheduled_task_id = None
            program.suspended_by_campaign = False
            program.suspended_at = None
            program.updated_at = _now()
            return {"program_id": program_id, "status": "completed", "scheduled": 0}
        target = plan[day_number - 1]
        if not recipients:
            task_ids = _pause_locked(
                session,
                program,
                reason="Нет активных адресов для текущего дневного этапа.",
            )
            program.scheduled_task_id = None
            pending_ids: list[str] = []
            should_pause = True
            schedule: list[datetime] = []
        else:
            existing_by_sequence = {
                int(delivery.sequence_number): delivery
                for delivery in existing
            }
            now = _now()
            if warmup_mode == FIXED_DAILY_MODE:
                existing_by_recipient = Counter(delivery.recipient_id for delivery in existing)
                desired_recipients: list[ConnectionWarmupRecipient] = []
                max_quota = max(max(1, int(recipient.messages_per_day or 1)) for recipient in recipients)
                for quota_index in range(max_quota):
                    for recipient in recipients:
                        quota = max(1, int(recipient.messages_per_day or 1))
                        already_planned = int(existing_by_recipient.get(recipient.id, 0))
                        if quota_index >= quota or quota_index < already_planned:
                            continue
                        desired_recipients.append(recipient)
                next_sequence = max(existing_by_sequence, default=0) + 1
            else:
                desired_recipients = [
                    recipients[(sequence_number - 1) % len(recipients)]
                    for sequence_number in range(1, target + 1)
                    if sequence_number not in existing_by_sequence
                ]
                next_sequence = min(
                    (sequence_number for sequence_number in range(1, target + 1) if sequence_number not in existing_by_sequence),
                    default=target + 1,
                )
            for recipient in desired_recipients:
                while next_sequence in existing_by_sequence:
                    next_sequence += 1
                delivery = ConnectionWarmupDelivery(
                    id=str(uuid4()),
                    program_id=program.id,
                    recipient_id=recipient.id,
                    day_number=day_number,
                    run_number=int(program.run_number or 1),
                    sequence_number=next_sequence,
                    status="queued",
                    scheduled_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(delivery)
                existing.append(delivery)
                existing_by_sequence[next_sequence] = delivery
                next_sequence += 1
            session.flush()
            pending_ids = [
                delivery.id
                for delivery in sorted(existing, key=lambda item: int(item.sequence_number))
                if delivery.status == "paused" or (delivery.status == "queued" and not delivery.task_id)
            ]
            task_ids = []
            should_pause = False
            program.scheduled_task_id = None
            program.updated_at = _now()
            schedule = _message_schedule(program, len(pending_ids))
    if should_pause:
        _cancel_task_ids(task_ids)
        return {"program_id": program_id, "status": "paused", "scheduled": 0}
    for delivery_id, available_at in zip(pending_ids, schedule, strict=True):
        _enqueue_delivery(delivery_id, available_at=available_at)
    if not pending_ids:
        _advance_after_day(program_id, day_number)
    return {
        "program_id": program_id,
        "status": "running",
        "day_number": day_number,
        "scheduled": len(pending_ids),
    }


def _advance_after_day(program_id: str, day_number: int) -> None:
    enqueue_next = False
    with session_scope() as session:
        program = session.execute(
            select(ConnectionWarmupProgram).where(ConnectionWarmupProgram.id == program_id).with_for_update()
        ).scalar_one_or_none()
        if program is None or program.status != "running" or int(program.current_day or 1) != day_number:
            return
        pending_count = int(session.scalar(
            select(func.count()).select_from(ConnectionWarmupDelivery).where(
                ConnectionWarmupDelivery.program_id == program_id,
                ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
                ConnectionWarmupDelivery.day_number == day_number,
                ConnectionWarmupDelivery.status.in_({"queued", "paused", "sending"}),
            )
        ) or 0)
        if pending_count:
            return
        program.current_day = day_number + 1
        if str(program.warmup_mode or GROWTH_MODE) == FIXED_DAILY_MODE:
            plan_length = max(1, min(MAX_WARMUP_DURATION_DAYS, int(program.duration_days or 14)))
        else:
            plan_length = len(list(program.daily_plan or DEFAULT_DAILY_PLAN))
        if program.current_day > plan_length:
            program.status = "completed"
            program.completed_at = _now()
            program.suspended_by_campaign = False
            program.suspended_at = None
            program.pause_reason = None
        else:
            enqueue_next = True
        program.updated_at = _now()
    if enqueue_next:
        _enqueue_day(program_id, immediate=False)


def run_warmup_message(kwargs: dict[str, Any]) -> dict[str, Any]:
    delivery_id = str(kwargs.get("delivery_id") or "").strip()
    if not delivery_id:
        raise ValueError("delivery_id is required")
    campaign_blocked = False
    blocking_campaigns: list[dict[str, str]] = []
    skip_delivery = False
    with session_scope() as session:
        delivery = session.execute(
            select(ConnectionWarmupDelivery).where(ConnectionWarmupDelivery.id == delivery_id).with_for_update()
        ).scalar_one_or_none()
        if delivery is None:
            raise LookupError("Письмо прогрева не найдено.")
        program = session.get(ConnectionWarmupProgram, delivery.program_id)
        recipient = session.get(ConnectionWarmupRecipient, delivery.recipient_id)
        if program is None or recipient is None:
            raise LookupError("Программа или получатель прогрева не найдены.")
        if delivery.status in {"accepted", "delivered", "hard_bounced", "soft_bounced", "complaint", "error", "cancelled"}:
            return {"delivery_id": delivery_id, "status": delivery.status}
        if delivery.status == "sending":
            # A previous attempt crashed mid-send (process killed/OOM/lease
            # expired) after this status was committed but before the network
            # call finished. We cannot know whether the email actually went
            # out, so we must not risk a duplicate by resending here.
            delivery.status = "error"
            delivery.error = (
                "Прервано во время отправки на предыдущей попытке; повторная "
                "отправка не выполнена во избежание дублирования."
            )
            delivery.task_id = None
            delivery.updated_at = _now()
            if recipient is not None:
                recipient.error_count = int(recipient.error_count or 0) + 1
                recipient.last_error = delivery.error
                recipient.updated_at = _now()
            return {"delivery_id": delivery_id, "status": "error"}
        # Remember the pre-send status so the exception handler below can
        # tell a first failure from a repeat one and only finalize as
        # terminal "error" once the task-queue's own max_attempts is
        # genuinely exhausted (see the final commit block further down).
        previous_status = delivery.status
        if program.status != "running":
            delivery.status = "paused"
            delivery.task_id = None
            delivery.updated_at = _now()
            return {"delivery_id": delivery_id, "status": "paused"}
        program_id = program.id
        day_number = int(delivery.day_number)
        task_ids: list[str] = []
        should_pause = False
        sending_connection = _selected_smtp_connection(session, program)
        blocking_campaigns = _active_rusender_campaigns(session, sending_connection)
        if blocking_campaigns:
            delivery.status = "paused"
            delivery.task_id = None
            delivery.updated_at = _now()
            _set_campaign_suspension(program, blocking_campaigns)
            campaign_blocked = True
        else:
            _clear_campaign_suspension(program)
        if not campaign_blocked and recipient.status != "active":
            replacement = session.scalar(
                select(ConnectionWarmupRecipient)
                .where(
                    ConnectionWarmupRecipient.program_id == program.id,
                    ConnectionWarmupRecipient.status == "active",
                )
                .order_by(
                    ConnectionWarmupRecipient.sent_count.asc(),
                    ConnectionWarmupRecipient.last_sent_at.asc().nullsfirst(),
                    ConnectionWarmupRecipient.created_at.asc(),
                )
                .limit(1)
            )
            if replacement is None:
                delivery.status = "cancelled"
                delivery.task_id = None
                delivery.updated_at = _now()
                task_ids = _pause_locked(
                    session,
                    program,
                    reason="Нет активных адресов для продолжения прогрева.",
                )
                should_pause = True
            elif str(program.warmup_mode or GROWTH_MODE) == FIXED_DAILY_MODE:
                delivery.status = "cancelled"
                delivery.task_id = None
                delivery.updated_at = _now()
                skip_delivery = True
            else:
                recipient = replacement
                delivery.recipient_id = replacement.id
        if not campaign_blocked and not should_pause and not skip_delivery:
            delivery.status = "sending"
            delivery.updated_at = _now()
            connection_id = str(program.smtp_connection_id or "")
            owner_username = program.owner_username
            email = recipient.email
            subjects = list(program.subject_templates or DEFAULT_SUBJECT_TEMPLATES)
            bodies = list(program.body_templates or DEFAULT_BODY_TEMPLATES)
            variant_index = (
                day_number + int(delivery.sequence_number or 0) + int(recipient.sent_count or 0)
            ) % min(len(subjects), len(bodies))
            subject = subjects[variant_index % len(subjects)]
            text = bodies[variant_index % len(bodies)]
            recipient_id = recipient.id

    if campaign_blocked:
        _enqueue_campaign_resume(program_id)
        return {
            "delivery_id": delivery_id,
            "status": "suspended_by_campaign",
            "campaigns": blocking_campaigns,
        }
    if should_pause:
        _cancel_task_ids(task_ids)
        return {"delivery_id": delivery_id, "status": "paused"}
    if skip_delivery:
        _advance_after_day(program_id, day_number)
        return {"delivery_id": delivery_id, "status": "cancelled"}
    from src.campaigns.batch_worker import _send_delivery_message

    try:
        message_id = _send_delivery_message(
            connection_id=connection_id,
            owner_username=owner_username,
            to_email=email,
            subject=subject,
            html=f"<p>{text}</p>",
            text=text,
            row_id=f"sender-warmup-{delivery_id}",
            send_mode="connection_warmup",
            track_links=False,
            tracking_key=f"sender-warmup:{delivery_id}",
            tracking_warmup_delivery_id=delivery_id,
        )
        status = "accepted"
        error = ""
    except WarmupSuspendedByCampaign as exc:
        with session_scope() as session:
            delivery = session.get(ConnectionWarmupDelivery, delivery_id)
            program = session.get(ConnectionWarmupProgram, program_id)
            if delivery is not None:
                delivery.status = "paused"
                delivery.task_id = None
                delivery.updated_at = _now()
            if program is not None:
                _set_campaign_suspension(program, exc.campaigns)
        _enqueue_campaign_resume(program_id)
        return {
            "delivery_id": delivery_id,
            "status": "suspended_by_campaign",
            "campaigns": exc.campaigns,
        }
    except Exception as exc:
        message_id = ""
        error = str(exc)[:4000]
        # Only finalize as genuinely terminal "error" on the second
        # consecutive failure of this delivery — matches _enqueue_delivery's
        # hardcoded max_attempts=2, so the task-queue's own retry actually
        # gets a chance to call _send_delivery_message again instead of
        # short-circuiting at the terminal-status check above. Known
        # limitation: this caps retry allowance at exactly one extra attempt;
        # revisit if max_attempts ever changes from 2.
        status = "error" if previous_status == "send_retry_pending" else "send_retry_pending"

    with session_scope() as session:
        delivery = session.execute(
            select(ConnectionWarmupDelivery).where(ConnectionWarmupDelivery.id == delivery_id).with_for_update()
        ).scalar_one_or_none()
        recipient = session.execute(
            select(ConnectionWarmupRecipient).where(ConnectionWarmupRecipient.id == recipient_id).with_for_update()
        ).scalar_one_or_none()
        if delivery is not None:
            delivery.status = status
            delivery.provider_message_id = message_id or None
            delivery.error = error or None
            delivery.sent_at = _now() if status == "accepted" else None
            delivery.task_id = None
            delivery.updated_at = _now()
        if recipient is not None:
            if status == "accepted":
                recipient.sent_count = int(recipient.sent_count or 0) + 1
                recipient.last_sent_at = _now()
                recipient.last_error = None
            else:
                recipient.error_count = int(recipient.error_count or 0) + 1
                recipient.last_error = error
            recipient.updated_at = _now()
    _advance_after_day(program_id, day_number)
    if status in {"error", "send_retry_pending"}:
        raise RuntimeError(error)
    return {"delivery_id": delivery_id, "status": status, "provider_message_id": message_id}


def _find_delivery_for_provider_id(session: Any, message_id: str) -> ConnectionWarmupDelivery | None:
    delivery = session.execute(
        select(ConnectionWarmupDelivery)
        .where(ConnectionWarmupDelivery.provider_message_id == message_id)
        .with_for_update()
    ).scalars().first()
    if delivery is not None or not message_id.startswith("mailing-agent:"):
        return delivery
    parts = message_id.split(":", 2)
    provider = parts[1].strip().lower() if len(parts) == 3 else ""
    if provider not in {"rusender", "mailopost"}:
        return None
    from src.generator.delivery.sender_agent import _build_provider_idempotency_key

    candidates = session.execute(
        select(ConnectionWarmupDelivery, ConnectionWarmupRecipient.email)
        .join(ConnectionWarmupRecipient, ConnectionWarmupRecipient.id == ConnectionWarmupDelivery.recipient_id)
        .where(ConnectionWarmupDelivery.status.in_({"accepted", "delivered", "hard_bounced", "soft_bounced", "complaint"}))
        .order_by(ConnectionWarmupDelivery.sent_at.desc().nullslast())
        .limit(1000)
        .with_for_update(of=ConnectionWarmupDelivery)
    ).all()
    for candidate, email in candidates:
        expected = _build_provider_idempotency_key(
            provider=provider,
            job_id=None,
            row_id=f"sender-warmup-{candidate.id}",
            recipient=email,
            send_mode="connection_warmup",
        )
        if expected == message_id:
            return candidate
    return None

def record_warmup_delivery_outcome(
    *,
    provider_message_id: str,
    provider_status: str,
    smtp_response: str = "",
) -> dict[str, Any] | None:
    from src.generator.delivery.provider_ids import normalize_provider_message_id

    message_id = normalize_provider_message_id(provider_message_id)
    if not message_id:
        return None
    raw_status = str(provider_status or "").strip().lower()
    if raw_status in {"opened", "open", "clicked", "click"}:
        with session_scope() as session:
            delivery = _find_delivery_for_provider_id(session, message_id)
            if delivery is None:
                return None
            delivery.opened_at = delivery.opened_at or _now()
            delivery.open_count = int(delivery.open_count or 0) + 1
            delivery.updated_at = _now()
            return {
                "delivery_id": delivery.id,
                "program_id": delivery.program_id,
                "status": raw_status,
                "paused": False,
                "pause_reason": "",
            }
    status_map = {
        "delivered": "delivered",
        "hard_bounced": "hard_bounced",
        "hard_bounce": "hard_bounced",
        "soft_bounced": "soft_bounced",
        "soft_bounce": "soft_bounced",
        "spam": "complaint",
        "complaint": "complaint",
        "complained": "complaint",
    }
    mapped = status_map.get(raw_status)
    if mapped is None:
        return None
    pause_reason = ""
    task_ids: list[str] = []
    with session_scope() as session:
        delivery = _find_delivery_for_provider_id(session, message_id)
        if delivery is None:
            return None
        if delivery.status == mapped:
            return {"delivery_id": delivery.id, "status": mapped}
        previous_status = delivery.status
        delivery.status = mapped
        delivery.error = str(smtp_response or "").strip() or None
        delivery.updated_at = _now()
        recipient = session.get(ConnectionWarmupRecipient, delivery.recipient_id)
        program = session.get(ConnectionWarmupProgram, delivery.program_id)
        if recipient is not None and mapped in {"hard_bounced", "soft_bounced", "complaint"}:
            if previous_status not in {"hard_bounced", "soft_bounced", "complaint"}:
                recipient.error_count = int(recipient.error_count or 0) + 1
            recipient.last_error = str(smtp_response or mapped)[:4000]
            if mapped in {"hard_bounced", "complaint"}:
                recipient.status = "disabled"
            recipient.updated_at = _now()
        if program is not None and program.status == "running":
            total = int(session.scalar(
                select(func.count()).select_from(ConnectionWarmupDelivery).where(
                    ConnectionWarmupDelivery.program_id == program.id,
                    ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
                    ConnectionWarmupDelivery.status.in_({"accepted", "delivered", "hard_bounced", "soft_bounced", "complaint"}),
                )
            ) or 0)
            hard = int(session.scalar(
                select(func.count()).select_from(ConnectionWarmupDelivery).where(
                    ConnectionWarmupDelivery.program_id == program.id,
                    ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
                    ConnectionWarmupDelivery.status == "hard_bounced",
                )
            ) or 0)
            soft = int(session.scalar(
                select(func.count()).select_from(ConnectionWarmupDelivery).where(
                    ConnectionWarmupDelivery.program_id == program.id,
                    ConnectionWarmupDelivery.run_number == int(program.run_number or 1),
                    ConnectionWarmupDelivery.status == "soft_bounced",
                )
            ) or 0)
            if mapped == "complaint":
                pause_reason = "Получена жалоба на спам. Прогрев остановлен для проверки."
            elif mapped == "hard_bounced" and total and hard / total >= 0.05:
                pause_reason = "Доля постоянных отказов достигла 5%. Проверьте список адресов."
            elif mapped == "soft_bounced" and soft >= 3:
                pause_reason = "Получено три временных отказа. Проверьте репутацию и сервер отправки."
            if pause_reason:
                task_ids = _pause_locked(session, program, reason=pause_reason)
        result = {
            "delivery_id": delivery.id,
            "program_id": delivery.program_id,
            "status": mapped,
            "paused": bool(pause_reason),
            "pause_reason": pause_reason,
        }
    if task_ids:
        _cancel_task_ids(task_ids)
    return result
