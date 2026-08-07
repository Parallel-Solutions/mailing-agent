from __future__ import annotations

import base64
import csv
import io
import json
import re
import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.generator.delivery.manager_actions import (
    ACTION_TYPES,
    append_report_history,
    latest_action_by_recipient,
    load_manager_actions,
    load_report_history,
)
from src.generator.delivery.sender_report import (
    PROVIDER_LABELS,
    RECIPIENT_ROLE_LABELS,
    _build_consent_rows,
    _build_delivery_rows,
    _campaign_metadata,
    _format_moscow_datetime,
    _is_sender_delivery_refresh_running,
    _load_sender_state,
    _normalize_provider_status,
    _normalize_recipient_role,
    _now_moscow,
    _provider_label,
    _safe_text,
    _start_sender_delivery_refresh,
    build_sender_delivery_report_xlsx,
)
from src.jobs import load_agent_state, resolve_job_paths
from src.jobs.job_docs import list_job_ids_with_sent_mail
from src.jobs.storage import normalize_job_id
from src.utils.logger import logger

STATISTICS_TIMEZONE = ZoneInfo("Europe/Moscow")

MANAGER_STATUS_DEFINITIONS: dict[str, dict[str, str]] = {
    "delivered": {"label": "Доставлено", "tone": "good", "category": "success"},
    "opened": {"label": "Открыто", "tone": "good", "category": "success"},
    "clicked": {"label": "Переход по ссылке", "tone": "good", "category": "success"},
    "email_broken": {"label": "Email не работает", "tone": "bad", "category": "problem"},
    "soft_bounce": {"label": "Временная ошибка", "tone": "warn", "category": "problem"},
    "delivery_error": {"label": "Ошибка доставки", "tone": "bad", "category": "problem"},
    "unsubscribed": {"label": "Отписался", "tone": "warn", "category": "warning"},
    "spam": {"label": "Жалоба / спам", "tone": "bad", "category": "warning"},
    "pending": {"label": "Ожидают статуса", "tone": "neutral", "category": "pending"},
    "no_data": {"label": "Нет данных от сервиса", "tone": "neutral", "category": "pending"},
}

TECHNICAL_TO_MANAGER_STATUS: dict[str, str] = {
    "delivered": "delivered",
    "ok_delivered": "delivered",
    "opened": "opened",
    "ok_read": "opened",
    "clicked": "clicked",
    "ok_link_visited": "clicked",
    "hard_bounced": "email_broken",
    "err_user_unknown": "email_broken",
    "err_user_inactive": "email_broken",
    "err_mailbox_full": "email_broken",
    "soft_bounced": "soft_bounce",
    "err_will_retry": "soft_bounce",
    "skip_dup_temp_unreachable": "soft_bounce",
    "err_delivery_failed": "delivery_error",
    "failed": "delivery_error",
    "rejected": "delivery_error",
    "not_delivered": "delivery_error",
    "unsubscribed": "unsubscribed",
    "ok_unsubscribed": "unsubscribed",
    "spam": "spam",
    "complaint": "spam",
    "ok_spam_folder": "spam",
    "err_spam_rejected": "spam",
    "err_spam_skipped": "spam",
    "pending": "pending",
    "queued": "pending",
    "sent": "pending",
    "accepted": "pending",
    "ok_sent": "pending",
    "processing": "pending",
    "success": "pending",
    "not_sent": "pending",
    "unknown": "no_data",
}

INTEREST_BY_STATUS: dict[str, str] = {
    "clicked": "high",
    "opened": "high",
    "delivered": "medium",
    "pending": "low",
    "no_data": "low",
    "soft_bounce": "low",
    "email_broken": "low",
    "delivery_error": "low",
    "unsubscribed": "low",
    "spam": "low",
}

RECOMMENDED_BY_STATUS: dict[str, str] = {
    "clicked": "call",
    "opened": "call",
    "delivered": "ready_contact",
    "email_broken": "find_another_email",
    "soft_bounce": "retry_later",
    "delivery_error": "manual_check",
    "pending": "wait",
    "no_data": "wait",
    "unsubscribed": "do_not_contact",
    "spam": "do_not_contact",
}

RECOMMENDED_LABELS = {
    "call": "Перезвонить",
    "ready_contact": "Готов к контакту",
    "find_another_email": "Найти другой email",
    "retry_later": "Повторить позже",
    "manual_check": "Проверить вручную",
    "wait": "Ожидать статус",
    "do_not_contact": "Не трогать",
}

INTEREST_LABELS = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}
INTEREST_TONES = {"high": "good", "medium": "warn", "low": "neutral"}

BOUNCE_REASON_LABELS = {
    "email_not_exists": "Email не существует",
    "mailbox_full": "Переполнен ящик",
    "temporary_error": "Временная ошибка сервера",
    "spam_block": "Блокировка как спам",
    "other": "Прочее",
}

EMAIL_DOMAIN_PROVIDERS = {
    "mail.ru": "Mail.ru",
    "inbox.ru": "Mail.ru",
    "list.ru": "Mail.ru",
    "bk.ru": "Mail.ru",
    "gmail.com": "Gmail",
    "googlemail.com": "Gmail",
    "yandex.ru": "Yandex",
    "ya.ru": "Yandex",
    "outlook.com": "Outlook",
    "hotmail.com": "Outlook",
    "live.com": "Outlook",
}

# Statistics are aggregated per company (one input-data record / row_id), not per
# email. A single company can have several emails (primary + fallback) with
# different provider statuses. We collapse them into one "best" status: if any of
# the company's emails reached the recipient, the whole company counts as reached.
# The tuple is ordered best -> worst; the earliest match wins.
COMPANY_STATUS_PRIORITY: tuple[str, ...] = (
    "clicked",
    "opened",
    "delivered",
    "pending",
    "no_data",
    "soft_bounce",
    "delivery_error",
    "email_broken",
    "unsubscribed",
    "spam",
)

# Company data comes from a future dedicated service. Until it exists we surface
# whatever the uploaded/collected data.xlsx already has and show this placeholder
# for the fields we cannot fill yet.
COMPANY_DATA_PLACEHOLDER = "Данные появятся позже"

# Company card fields (technical key -> Russian label) surfaced in statistics.
COMPANY_FIELD_LABELS: dict[str, str] = {
    "region": "Субъект РФ",
    "district": "Муниципальный район",
    "settlement": "Муниципальное образование",
    "admin_name": "Администрация",
    "address": "Адрес",
    "head": "Глава",
    "population": "Население",
    "email_primary": "Email (основной)",
    "email_extra": "Email (доп.)",
    "phone": "Телефон",
    "phone_extra": "Доп. телефон",
    "inn": "ИНН",
    "kpp": "КПП",
    "ogrn": "ОГРН",
    "okpo": "ОКПО",
    "oktmo": "ОКТМО",
    "note": "Примечание",
}

# Map company field -> source columns in data.xlsx (excel_io aliases OKTNO/OKTMO).
COMPANY_SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "region": ("SUB_RF",),
    "district": ("MUN_R_NAME",),
    "settlement": ("MUN_NAME",),
    "admin_name": ("ADM_NAME",),
    "address": ("ADRES",),
    "head": ("HEAD_FIO",),
    "population": ("POPULATION",),
    "email_primary": ("EMAIL_OSN",),
    "email_extra": ("EMAIL_DOP",),
    "phone": ("TEL_OSN",),
    "phone_extra": ("TEL_DOP",),
    "inn": ("REQUISITES_INN",),
    "kpp": ("REQUISITES_KPP",),
    "ogrn": ("REQUISITES_OGRN",),
    "okpo": ("REQUISITES_OKPO",),
    "oktmo": ("REQUISITES_OKTMO", "REQUISITES_OKTNO"),
    "note": ("NOTE",),
}


@dataclass(frozen=True)
class StatsFilters:
    job_ids: tuple[str, ...]
    period_from: str = ""
    period_to: str = ""
    providers: tuple[str, ...] = ()
    manager_statuses: tuple[str, ...] = ()
    recipient_roles: tuple[str, ...] = ()
    consent_status: str = ""
    manager_action: str = ""
    organization: str = ""
    problems_only: bool = False
    q: str = ""
    quick_filter: str = ""


def normalize_manager_status(provider_status: str) -> dict[str, str]:
    normalized = _normalize_provider_status(provider_status)
    key = TECHNICAL_TO_MANAGER_STATUS.get(normalized, "no_data")
    definition = MANAGER_STATUS_DEFINITIONS[key]
    return {
        "key": key,
        "label": definition["label"],
        "tone": definition["tone"],
        "category": definition["category"],
    }


def interest_for(manager_status_key: str) -> dict[str, str]:
    level = INTEREST_BY_STATUS.get(manager_status_key, "low")
    return {
        "key": level,
        "label": INTEREST_LABELS[level],
        "tone": INTEREST_TONES[level],
    }


def recommended_action_for(manager_status_key: str) -> dict[str, str]:
    key = RECOMMENDED_BY_STATUS.get(manager_status_key, "wait")
    return {"key": key, "label": RECOMMENDED_LABELS[key]}


def make_row_key(job_id: str, row_id: str, recipient_email: str) -> str:
    raw = f"{normalize_job_id(job_id)}|{_safe_text(row_id)}|{_safe_text(recipient_email).lower()}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def parse_row_key(row_key: str) -> tuple[str, str, str]:
    padding = "=" * (-len(row_key) % 4)
    decoded = base64.urlsafe_b64decode(f"{row_key}{padding}").decode("utf-8")
    job_id, row_id, recipient = decoded.split("|", 2)
    return job_id, row_id, recipient


def _parse_datetime(value: Any) -> datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_period_date(value: Any) -> datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Statistics period boundaries must use YYYY-MM-DD format.") from exc


def normalize_statistics_period(
    period_from: Any = "",
    period_to: Any = "",
) -> tuple[str, str]:
    start = _parse_period_date(period_from)
    end = _parse_period_date(period_to)
    if start and end and start > end:
        raise ValueError("Statistics period start cannot be later than its end.")
    return (
        start.date().isoformat() if start else "",
        end.date().isoformat() if end else "",
    )


@lru_cache(maxsize=128)
def _statistics_period_bounds(
    period_from: str,
    period_to: str,
) -> tuple[datetime | None, datetime | None]:
    normalized_from, normalized_to = normalize_statistics_period(period_from, period_to)
    start = _parse_period_date(normalized_from)
    end = _parse_period_date(normalized_to)
    start_utc = (
        start.replace(tzinfo=STATISTICS_TIMEZONE).astimezone(timezone.utc)
        if start
        else None
    )
    end_utc = (
        (end + timedelta(days=1))
        .replace(tzinfo=STATISTICS_TIMEZONE)
        .astimezone(timezone.utc)
        if end
        else None
    )
    return start_utc, end_utc


def _statistics_event_datetime(value: Any) -> datetime | None:
    dt = _parse_datetime(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=STATISTICS_TIMEZONE)
    return dt.astimezone(timezone.utc)


def _within_period(value: Any, *, period_from: str, period_to: str) -> bool:
    if not period_from and not period_to:
        return True
    dt = _statistics_event_datetime(value)
    if dt is None:
        return False
    start, end = _statistics_period_bounds(period_from, period_to)
    if start and dt < start:
        return False
    if end and dt >= end:
        return False
    return True


def _filter_rows_by_period(
    rows: list[dict[str, Any]],
    *,
    period_from: str,
    period_to: str,
    timestamp_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not period_from and not period_to:
        return rows
    return [
        row
        for row in rows
        if _within_period(
            next(
                (
                    row.get(field)
                    for field in timestamp_fields
                    if _safe_text(row.get(field))
                ),
                "",
            ),
            period_from=period_from,
            period_to=period_to,
        )
    ]



def _pct(value: int, base: int) -> float:
    if base <= 0:
        return 0.0
    return round((value / base) * 100, 1)


def _manager_action_view(record: dict[str, Any] | None) -> dict[str, str]:
    if not record:
        return {"key": "", "label": ""}
    action_type = _safe_text(record.get("action_type"))
    return {
        "key": action_type,
        "label": _safe_text(record.get("action_label")) or ACTION_TYPES.get(action_type, action_type),
    }


def _bounce_reason(provider_status: str, delivery_response: str = "") -> str:
    normalized = _normalize_provider_status(provider_status)
    response = _safe_text(delivery_response).lower()
    if normalized in {"err_user_unknown", "err_user_inactive", "hard_bounced"} or "user unknown" in response:
        return "email_not_exists"
    if normalized == "err_mailbox_full" or "mailbox full" in response:
        return "mailbox_full"
    if normalized in {"soft_bounced", "err_will_retry", "skip_dup_temp_unreachable"}:
        return "temporary_error"
    if normalized in {"spam", "complaint", "err_spam_rejected", "err_spam_skipped", "ok_spam_folder"}:
        return "spam_block"
    return "other"


def _email_domain_provider(email: str) -> str:
    domain = _safe_text(email).split("@")[-1].lower()
    if not domain:
        return "Другие"
    return EMAIL_DOMAIN_PROVIDERS.get(domain, "Другие")


# --- In-memory cache + background warm -------------------------------------------
# Every statistics endpoint (dashboard, recipients, problems, campaigns,
# analytics) needs the same per-job delivery/consent rows, and each rebuild is
# expensive (DB reads + provider event files + state files). Cache the enriched
# rows per job so a single page load with several API calls does not recompute
# the same jobs repeatedly. A background warm loop refreshes the cache every
# 20 minutes; TTL is slightly longer to avoid gaps between warm cycles.
_CACHE_WARM_INTERVAL_SECONDS = 20 * 60
_CACHE_TTL_SECONDS = float(25 * 60)
_cache_lock = threading.Lock()
_cache_build_locks: dict[tuple[str, str], threading.Lock] = {}
_delivery_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_consent_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
# Company data (data.xlsx joined by row_id). Cached like delivery/consent rows so
# a single page load does not re-read/rebuild the workbook for every job.
_company_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
# Jobs for which a background provider refresh was requested. When that refresh
# finishes we invalidate the job's cache once so the freshly pulled events show
# up on the next read (see _settle_refreshed_job).
_pending_invalidations: set[str] = set()
_stats_cache_warm_thread: threading.Thread | None = None
_stats_cache_warm_thread_lock = threading.Lock()
_stats_cache_warm_stop_event = threading.Event()


def warm_stats_cache() -> dict[str, int]:
    """Pre-load delivery/consent rows for all jobs with sent mail (no provider API)."""

    started = time.monotonic()
    job_ids = tuple(list_job_ids_with_sent_mail())
    if job_ids:
        _load_delivery_for_jobs(job_ids)
        _load_consents_for_jobs(job_ids)
    duration_ms = int((time.monotonic() - started) * 1000)
    result = {"jobs": len(job_ids), "duration_ms": duration_ms}
    logger.info("stats_cache_warm_completed", **result)
    return result


def _run_stats_cache_warm_loop(interval_seconds: int) -> None:
    poll_seconds = max(60, int(interval_seconds or _CACHE_WARM_INTERVAL_SECONDS))
    while not _stats_cache_warm_stop_event.is_set():
        try:
            warm_stats_cache()
        except Exception as exc:
            logger.exception("stats_cache_warm_failed", error=str(exc))
        _stats_cache_warm_stop_event.wait(poll_seconds)


def start_stats_cache_warm_loop(*, interval_seconds: int = _CACHE_WARM_INTERVAL_SECONDS) -> None:
    """Start daemon thread that warms statistics cache on startup and every interval."""

    with _stats_cache_warm_thread_lock:
        global _stats_cache_warm_thread
        if _stats_cache_warm_thread and _stats_cache_warm_thread.is_alive():
            return
        _stats_cache_warm_stop_event.clear()
        _stats_cache_warm_thread = threading.Thread(
            target=_run_stats_cache_warm_loop,
            kwargs={"interval_seconds": interval_seconds},
            daemon=True,
            name="stats-cache-warm",
        )
        _stats_cache_warm_thread.start()


def stop_stats_cache_warm_loop() -> None:
    _stats_cache_warm_stop_event.set()


def _cache_get(store: dict[str, tuple[float, list[dict[str, Any]]]], job_id: str) -> list[dict[str, Any]] | None:
    with _cache_lock:
        entry = store.get(job_id)
        if entry is None:
            return None
        cached_at, value = entry
        if (time.monotonic() - cached_at) >= _CACHE_TTL_SECONDS:
            store.pop(job_id, None)
            return None
        return value


def _cache_set(store: dict[str, tuple[float, list[dict[str, Any]]]], job_id: str, value: list[dict[str, Any]]) -> None:
    with _cache_lock:
        store[job_id] = (time.monotonic(), value)


def _cache_build_lock(namespace: str, job_id: str) -> threading.Lock:
    with _cache_lock:
        return _cache_build_locks.setdefault((namespace, job_id), threading.Lock())


def invalidate_stats_cache(job_id: str | None = None) -> None:
    """Drop cached delivery/consent rows so the next read rebuilds from source."""

    with _cache_lock:
        if job_id is None:
            _delivery_cache.clear()
            _consent_cache.clear()
            _company_cache.clear()
            return
        _delivery_cache.pop(job_id, None)
        _consent_cache.pop(job_id, None)
        _company_cache.pop(job_id, None)


def _mark_pending_refresh(job_id: str) -> None:
    with _cache_lock:
        _pending_invalidations.add(job_id)


def _settle_refreshed_job(job_id: str) -> None:
    """If a requested background refresh has finished, rebuild the cache once."""

    with _cache_lock:
        pending = job_id in _pending_invalidations
    if not pending or _is_sender_delivery_refresh_running(job_id):
        return
    with _cache_lock:
        _pending_invalidations.discard(job_id)
        _delivery_cache.pop(job_id, None)
        _consent_cache.pop(job_id, None)
        _company_cache.pop(job_id, None)


_BACKGROUND_REFRESH_LIMIT = 25


def _job_awaiting_provider_events(rows: list[dict[str, Any]]) -> bool:
    """True when a job has sends but no provider events yet (all pending)."""

    if not rows:
        # No sent mail for this job → nothing to fetch from providers.
        return False
    for row in rows:
        if row.get("manager_status", {}).get("key") not in {"pending", "no_data"}:
            return False
    return True


def _trigger_provider_refresh(
    job_ids: tuple[str, ...],
    rows_by_job: dict[str, list[dict[str, Any]]],
    *,
    manual: bool,
    auto: bool,
) -> tuple[bool, bool]:
    """Start background provider dooбор for the relevant jobs.

    Returns (refresh_started, refresh_in_progress). Bounded by
    _BACKGROUND_REFRESH_LIMIT so an "all campaigns" view never spawns hundreds of
    provider API calls at once — webhooks keep the rest up to date.
    """

    started = False
    triggered = 0
    for job_id in job_ids:
        if triggered >= _BACKGROUND_REFRESH_LIMIT:
            break
        wants = manual or (auto and _job_awaiting_provider_events(rows_by_job.get(job_id, [])))
        if not wants:
            continue
        triggered += 1
        if _start_sender_delivery_refresh(job_id):
            started = True
        _mark_pending_refresh(job_id)
    in_progress = any(_is_sender_delivery_refresh_running(job_id) for job_id in job_ids)
    return started, in_progress


def _company_display_name(source: dict[str, Any], row_id: str) -> str:
    """Best available company name (data.xlsx has no dedicated company service yet)."""
    for key in ("MUN_NAME", "ADM_NAME", "MUN_R_NAME"):
        name = _safe_text(source.get(key))
        if name:
            return name
    return f"Компания №{row_id}" if row_id else "Без названия"


def _company_info_from_source(source: dict[str, Any]) -> dict[str, str]:
    info: dict[str, str] = {}
    for field, keys in COMPANY_SOURCE_FIELDS.items():
        value = ""
        for key in keys:
            value = _safe_text(source.get(key))
            if value:
                break
        info[field] = value
    return info


def _load_company_data_for_job(job_id: str) -> dict[str, dict[str, Any]]:
    """Load per-company data (keyed by row_id) from the job's data.xlsx.

    Returns ``{row_id: {"name": str, "info": {field: value}}}``. Missing/absent
    data is tolerated: statistics fall back to placeholders when a row is absent.
    For CampaignFlow jobs without data.xlsx coverage, fill from campaign_recipients.
    """

    cached = _cache_get(_company_cache, job_id)
    if cached is not None:
        return cached
    data: dict[str, dict[str, Any]] = {}
    try:
        from src.generator.delivery.sender_report import _report_data_xlsx_path
        from src.generator.generation.excel_io import load_rows

        path = _report_data_xlsx_path(job_id)
        if path.exists():
            _, _, rows = load_rows(path)
            for source in rows:
                row_id = _safe_text(source.get("ID"))
                if not row_id:
                    continue
                data[row_id] = {
                    "name": _company_display_name(source, row_id),
                    "info": _company_info_from_source(source),
                }
    except Exception as exc:  # pragma: no cover - defensive, data may be missing
        logger.warning("company_data_load_failed", job_id=job_id, error=str(exc))
    try:
        _merge_campaign_recipient_company_data(job_id, data)
    except Exception as exc:  # pragma: no cover
        logger.warning("campaign_recipient_company_data_failed", job_id=job_id, error=str(exc))
    _cache_set(_company_cache, job_id, data)
    return data


def _merge_campaign_recipient_company_data(job_id: str, data: dict[str, dict[str, Any]]) -> None:
    """Fill gaps from CampaignFlow recipients (keyed by recipient.id as row_id)."""

    from sqlalchemy import select

    from src.infra.db import session_scope
    from src.infra.models import Campaign, CampaignRecipient

    with session_scope() as session:
        campaign = session.scalar(select(Campaign).where(Campaign.job_id == job_id).limit(1))
        if campaign is None:
            return
        recipients = session.scalars(
            select(CampaignRecipient).where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.excluded.is_(False),
            )
        ).all()
        for recipient in recipients:
            row_id = _safe_text(recipient.id)
            if not row_id:
                continue
            extra = dict(recipient.extra or {}) if isinstance(recipient.extra, dict) else {}
            info = {
                "region": _safe_text(recipient.region) or _safe_text(extra.get("region")),
                "district": _safe_text(extra.get("district")),
                "settlement": _safe_text(extra.get("settlement") or extra.get("mun_name")),
                "admin_name": _safe_text(extra.get("admin_name")),
                "address": _safe_text(extra.get("address")),
                "head": _safe_text(recipient.contact_name) or _safe_text(extra.get("head")),
                "population": _safe_text(extra.get("population")),
                "email_primary": _safe_text(recipient.email),
                "email_extra": _safe_text(recipient.email_fallback),
                "phone": _safe_text(extra.get("phone") or extra.get("tel_osn")),
                "phone_extra": _safe_text(extra.get("phone_extra") or extra.get("tel_dop")),
                "inn": _safe_text(extra.get("inn")),
                "kpp": _safe_text(extra.get("kpp")),
                "ogrn": _safe_text(extra.get("ogrn")),
                "okpo": _safe_text(extra.get("okpo")),
                "oktmo": _safe_text(extra.get("oktmo")),
                "note": _safe_text(extra.get("note")),
            }
            existing = data.get(row_id)
            if existing is None:
                data[row_id] = {
                    "name": _safe_text(recipient.company) or f"Компания №{row_id}",
                    "info": {key: value for key, value in info.items() if value},
                }
                continue
            # Prefer Excel name/info; fill only blank fields from recipient.
            merged_info = dict(existing.get("info") or {})
            for key, value in info.items():
                if value and not _safe_text(merged_info.get(key)):
                    merged_info[key] = value
            existing["info"] = merged_info
            if not _safe_text(existing.get("name")) and _safe_text(recipient.company):
                existing["name"] = _safe_text(recipient.company)


def _company_view(company_entry: dict[str, Any] | None, row_id: str, fallback_org: str) -> dict[str, Any]:
    """Build the company card payload with generic placeholders for empty fields."""
    source_info = (company_entry or {}).get("info", {})
    fields: dict[str, dict[str, Any]] = {}
    for field, label in COMPANY_FIELD_LABELS.items():
        value = _safe_text(source_info.get(field))
        fields[field] = {
            "label": label,
            "value": value,
            "display": value or COMPANY_DATA_PLACEHOLDER,
            "present": bool(value),
        }
    name = (
        _safe_text((company_entry or {}).get("name"))
        or fallback_org
        or (f"Компания №{row_id}" if row_id else "Без названия")
    )
    return {"row_id": row_id, "name": name, "fields": fields}


def _campaign_recipient_lookup(job_id: str) -> dict[str, Any] | None:
    """Resolve historical CampaignFlow rows to current recipient ids."""

    from sqlalchemy import select

    from src.campaigns.recipient_email_service import parse_email_candidates
    from src.infra.db import session_scope
    from src.infra.models import Campaign, CampaignRecipient

    with session_scope() as session:
        campaign = session.scalar(select(Campaign).where(Campaign.job_id == job_id).limit(1))
        if campaign is None:
            return None
        recipients = session.scalars(
            select(CampaignRecipient).where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.excluded.is_(False),
            )
        ).all()
        by_id: dict[str, int] = {}
        by_source_row: dict[str, int] = {}
        by_email: dict[str, int] = {}
        duplicate_emails: set[str] = set()
        for recipient in recipients:
            recipient_id = int(recipient.id)
            by_id[str(recipient_id)] = recipient_id
            by_source_row[str(int(recipient.row_index) + 1)] = recipient_id
            for email in parse_email_candidates(recipient.email, recipient.email_fallback):
                key = _safe_text(email).lower()
                if not key:
                    continue
                if key in by_email and by_email[key] != recipient_id:
                    duplicate_emails.add(key)
                else:
                    by_email[key] = recipient_id
        for email in duplicate_emails:
            by_email.pop(email, None)
        return {
            "campaign_id": _safe_text(campaign.id),
            "by_id": by_id,
            "by_source_row": by_source_row,
            "by_email": by_email,
        }


def _build_delivery_rows_for_job(job_id: str, *, refresh: bool) -> list[dict[str, Any]]:
    # CampaignFlow writes sent_mail_log without legacy sender-state / send_run scope.
    # Manager statistics must aggregate the full mailing history from job_events.
    delivery_rows, _ = _build_delivery_rows(job_id, refresh=refresh, for_statistics=True)
    recipient_lookup = _campaign_recipient_lookup(job_id)
    if recipient_lookup is not None:
        current_rows: list[dict[str, Any]] = []
        for row in delivery_rows:
            email = _safe_text(row.get("recipient") or row.get("email")).lower()
            recipient_id = recipient_lookup["by_email"].get(email) if email else None
            if recipient_id is None and not email:
                source_row_id = _safe_text(row.get("row_id"))
                recipient_id = recipient_lookup["by_id"].get(source_row_id)
                if recipient_id is None:
                    recipient_id = recipient_lookup["by_source_row"].get(source_row_id)
            if recipient_id is None:
                continue
            current_rows.append(
                {
                    **row,
                    "row_id": str(recipient_id),
                    "campaign_id": recipient_lookup["campaign_id"],
                }
            )
        delivery_rows = current_rows
    latest_actions = latest_action_by_recipient(job_id)
    company_data = _load_company_data_for_job(job_id)
    rows: list[dict[str, Any]] = []
    for row in delivery_rows:
        manager_status = normalize_manager_status(row.get("provider_status") or "unknown")
        interest = interest_for(manager_status["key"])
        recommended = recommended_action_for(manager_status["key"])
        action = latest_actions.get((_safe_text(row.get("row_id")), _safe_text(row.get("recipient")).lower()))
        next_action = _manager_action_view(action) if action else recommended
        row_id = _safe_text(row.get("row_id"))
        company = _company_view(company_data.get(row_id), row_id, _safe_text(row.get("mun_name")))
        rows.append(
            {
                **row,
                "job_id": job_id,
                "row_key": make_row_key(job_id, row.get("row_id"), row.get("recipient")),
                "company": company,
                "organization": company["name"],
                "recipient_name": _safe_text(row.get("recipient")),
                "email": _safe_text(row.get("recipient")).lower(),
                "role": _normalize_recipient_role(row.get("recipient_role")),
                "role_label": RECIPIENT_ROLE_LABELS.get(
                    _normalize_recipient_role(row.get("recipient_role")),
                    RECIPIENT_ROLE_LABELS["unknown"],
                ),
                "manager_status": manager_status,
                "interest": interest,
                "recommended_action": recommended,
                "next_action": next_action,
                "last_event_at": _safe_text(row.get("checked_at") or row.get("sent_at")),
                "last_event_label": manager_status["label"],
                "attempts": 1,
                "bounce_reason": _bounce_reason(row.get("provider_status"), row.get("delivery_response")),
                "bounce_reason_label": BOUNCE_REASON_LABELS.get(
                    _bounce_reason(row.get("provider_status"), row.get("delivery_response")),
                    "Прочее",
                ),
                "email_domain_provider": _email_domain_provider(_safe_text(row.get("recipient"))),
                "layout_error_code": _safe_text(row.get("layout_error_code")),
                "error": _safe_text(row.get("error")),
                "comment": _safe_text(row.get("comment")),
            }
        )
    return rows


def _load_delivery_for_jobs(job_ids: tuple[str, ...], *, refresh: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job_id in job_ids:
        if refresh:
            invalidate_stats_cache(job_id)
        else:
            _settle_refreshed_job(job_id)
        cached = None if refresh else _cache_get(_delivery_cache, job_id)
        if cached is None:
            with _cache_build_lock("delivery", job_id):
                cached = None if refresh else _cache_get(_delivery_cache, job_id)
                if cached is None:
                    cached = _build_delivery_rows_for_job(job_id, refresh=refresh)
                    _cache_set(_delivery_cache, job_id, cached)
        rows.extend(cached)
    return rows


def _build_consent_rows_for_job(job_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _build_consent_rows(job_id):
        status_key = _safe_text(row.get("status")) or "pending"
        materials_status = _safe_text(row.get("materials_status"))
        interest_key = "high" if status_key == "confirmed" and materials_status == "sent" else "medium"
        if status_key != "confirmed":
            interest_key = "low"
        rows.append(
            {
                **row,
                "job_id": job_id,
                "organization": _safe_text(row.get("mun_name")),
                "contact": _safe_text(row.get("recipient")),
                "email": _safe_text(row.get("recipient")).lower(),
                "consent_status_key": status_key,
                "consent_status_label": _safe_text(row.get("status_label")),
                "materials_label": _safe_text(row.get("materials_status_label")),
                "last_action_label": _safe_text(row.get("materials_status_label") or row.get("status_label")),
                "last_action_at": _safe_text(row.get("materials_sent_at") or row.get("confirmed_at") or row.get("request_sent_at")),
                "interest": {
                    "key": interest_key,
                    "label": INTEREST_LABELS[interest_key],
                    "tone": INTEREST_TONES[interest_key],
                },
                "next_action": recommended_action_for("opened" if status_key == "confirmed" else "pending"),
            }
        )
    rows.extend(_build_chain_consent_rows_for_job(job_id, existing_emails={_safe_text(r.get("email")).lower() for r in rows}))
    return rows


def _build_chain_consent_rows_for_job(job_id: str, *, existing_emails: set[str]) -> list[dict[str, Any]]:
    """Map CampaignFlow chain subscribe events into consent KPI rows (no duplicates by email)."""

    from sqlalchemy import select

    from src.campaigns.chain_consent_service import ACTION_SUBSCRIBE
    from src.infra.db import session_scope
    from src.infra.models import Campaign, CampaignChainConsentEvent, CampaignRecipient
    from src.generator.delivery.sender_report import _format_moscow_datetime

    rows: list[dict[str, Any]] = []
    with session_scope() as session:
        campaign = session.scalar(select(Campaign).where(Campaign.job_id == job_id).limit(1))
        if campaign is None:
            return rows
        events = session.scalars(
            select(CampaignChainConsentEvent)
            .where(
                CampaignChainConsentEvent.campaign_id == campaign.id,
                CampaignChainConsentEvent.action == ACTION_SUBSCRIBE,
            )
            .order_by(CampaignChainConsentEvent.created_at.desc())
        ).all()
        recipient_ids = {int(event.recipient_id) for event in events if event.recipient_id is not None}
        recipients = {}
        if recipient_ids:
            for recipient in session.scalars(
                select(CampaignRecipient).where(CampaignRecipient.id.in_(recipient_ids))
            ).all():
                recipients[int(recipient.id)] = recipient

        seen_emails = set(existing_emails)
        for event in events:
            email = _safe_text(event.email).lower()
            if not email or email in seen_emails:
                continue
            seen_emails.add(email)
            recipient = recipients.get(int(event.recipient_id)) if event.recipient_id is not None else None
            organization = _safe_text(recipient.company) if recipient is not None else ""
            confirmed_at = _format_moscow_datetime(event.created_at.isoformat() if event.created_at else "")
            rows.append(
                {
                    "row_id": _safe_text(event.recipient_id),
                    "mun_name": organization,
                    "recipient": email,
                    "status": "confirmed",
                    "status_label": "Подтверждено (цепочка)",
                    "request_sent_at": "",
                    "created_at": confirmed_at,
                    "confirmed_at": confirmed_at,
                    "expires_at": _format_moscow_datetime(event.expires_at.isoformat() if event.expires_at else ""),
                    "materials_status": "",
                    "materials_status_label": "",
                    "materials_sent_at": "",
                    "materials_error": "",
                    "materials_dispatch_summary": "",
                    "transport": _safe_text(campaign.transport),
                    "attachment_mode": "",
                    "work_type": "",
                    "confirmed_ip": "",
                    "confirmed_user_agent": "",
                    "consent_document_path": "",
                    "job_id": job_id,
                    "organization": organization,
                    "contact": _safe_text(recipient.contact_name) if recipient is not None else email,
                    "email": email,
                    "consent_status_key": "confirmed",
                    "consent_status_label": "Подтверждено (цепочка)",
                    "materials_label": "",
                    "last_action_label": "Подписка в цепочке",
                    "last_action_at": confirmed_at,
                    "interest": {
                        "key": "medium",
                        "label": INTEREST_LABELS["medium"],
                        "tone": INTEREST_TONES["medium"],
                    },
                    "next_action": recommended_action_for("opened"),
                    "source": "chain",
                }
            )
    return rows


def _load_consents_for_jobs(job_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job_id in job_ids:
        cached = _cache_get(_consent_cache, job_id)
        if cached is None:
            with _cache_build_lock("consent", job_id):
                cached = _cache_get(_consent_cache, job_id)
                if cached is None:
                    cached = _build_consent_rows_for_job(job_id)
                    _cache_set(_consent_cache, job_id, cached)
        rows.extend(cached)
    return rows


# --- Company aggregation ---------------------------------------------------------
# Statistics group per-email delivery rows into one row per company (row_id) so
# every view (dashboard, recipients, campaigns, analytics, problems) counts
# companies rather than individual emails.


def _company_status_rank(key: str) -> int:
    try:
        return COMPANY_STATUS_PRIORITY.index(key)
    except ValueError:
        return len(COMPANY_STATUS_PRIORITY)


def _manager_status_view(key: str) -> dict[str, str]:
    resolved = key if key in MANAGER_STATUS_DEFINITIONS else "no_data"
    definition = MANAGER_STATUS_DEFINITIONS[resolved]
    return {
        "key": resolved,
        "label": definition["label"],
        "tone": definition["tone"],
        "category": definition["category"],
    }


def _build_company_row(group: list[dict[str, Any]]) -> dict[str, Any]:
    first = group[0]
    job_id = _safe_text(first.get("job_id"))
    row_id = _safe_text(first.get("row_id"))
    # Best status wins: the company is "reached" if any of its emails worked.
    best_key = min(
        (row.get("manager_status", {}).get("key") or "no_data" for row in group),
        key=_company_status_rank,
    )

    def _rep_sort(row: dict[str, Any]) -> tuple[int, int]:
        return (
            _company_status_rank(row.get("manager_status", {}).get("key") or "no_data"),
            0 if row.get("role") == "primary" else 1,
        )

    # Representative row produced the best status (keeps its manual next_action /
    # bounce reason); on ties prefer the primary email.
    representative = sorted(group, key=_rep_sort)[0]
    # Action target: prefer the primary email so manager actions land on the main
    # contact of the company.
    primary_row = next((row for row in group if row.get("role") == "primary"), representative)

    emails = [
        {
            "email": _safe_text(row.get("email")),
            "role": _safe_text(row.get("role")),
            "role_label": _safe_text(row.get("role_label")),
            "manager_status": row.get("manager_status", {}),
            "last_event_at": _safe_text(row.get("last_event_at")),
            "provider": _safe_text(row.get("provider")),
            "provider_label": _provider_label(_safe_text(row.get("provider"))),
            "bounce_reason_label": _safe_text(row.get("bounce_reason_label")),
        }
        for row in group
    ]

    timestamps = [
        _parse_datetime(row.get("sent_at_timestamp") or row.get("sent_at")) for row in group
    ]
    timestamps = [item for item in timestamps if item is not None]
    earliest = min(timestamps) if timestamps else None

    providers: list[str] = []
    for row in group:
        provider = _safe_text(row.get("provider"))
        if provider and provider not in providers:
            providers.append(provider)

    manager_status = _manager_status_view(best_key)
    company = first.get("company") or _company_view(None, row_id, _safe_text(first.get("organization")))
    organization = _safe_text(first.get("organization")) or _safe_text(company.get("name"))
    layout_error_code = ""
    for row in group:
        code = _safe_text(row.get("layout_error_code"))
        if code:
            layout_error_code = code
            break
    return {
        "job_id": job_id,
        "campaign_id": _safe_text(first.get("campaign_id")),
        "row_id": row_id,
        "row_key": _safe_text(primary_row.get("row_key")) or _safe_text(representative.get("row_key")),
        "company": company,
        "organization": organization,
        "recipient_name": organization,
        "email": _safe_text(primary_row.get("email")) or _safe_text(representative.get("email")),
        "emails": emails,
        "emails_text": " ".join(item["email"] for item in emails if item["email"]),
        "email_count": len(emails),
        "role": _safe_text(representative.get("role")),
        "role_label": _safe_text(representative.get("role_label")),
        "manager_status": manager_status,
        "interest": interest_for(best_key),
        "recommended_action": recommended_action_for(best_key),
        "next_action": representative.get("next_action") or recommended_action_for(best_key),
        "last_event_at": _safe_text(representative.get("last_event_at")),
        "last_event_label": manager_status["label"],
        "attempts": len(group),
        "provider": _safe_text(representative.get("provider")),
        "providers": providers,
        "bounce_reason": _safe_text(representative.get("bounce_reason")),
        "bounce_reason_label": _safe_text(representative.get("bounce_reason_label")),
        "email_domain_provider": _safe_text(representative.get("email_domain_provider")),
        "sent_at": _safe_text(representative.get("sent_at")),
        "sent_at_timestamp": earliest.isoformat() if earliest else _safe_text(representative.get("sent_at_timestamp")),
        "checked_at": _safe_text(representative.get("checked_at")),
        "work_type": _safe_text(first.get("work_type")),
        "campaign_name": _safe_text(first.get("campaign_name")),
        "subject": _safe_text(first.get("subject")),
        "layout_error_code": layout_error_code,
    }


def _group_rows_into_companies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (_safe_text(row.get("job_id")), _safe_text(row.get("row_id")))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    return [_build_company_row(groups[key]) for key in order]


def _load_companies_for_jobs(job_ids: tuple[str, ...], *, refresh: bool = False) -> list[dict[str, Any]]:
    return _group_rows_into_companies(_load_delivery_for_jobs(job_ids, refresh=refresh))


CONSENT_STATUS_PRIORITY: tuple[str, ...] = ("confirmed", "pending", "declined", "expired", "revoked")


def _consent_status_rank(key: str) -> int:
    try:
        return CONSENT_STATUS_PRIORITY.index(key)
    except ValueError:
        return len(CONSENT_STATUS_PRIORITY)


def _group_consents_into_companies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (_safe_text(row.get("job_id")), _safe_text(row.get("row_id")))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    companies: list[dict[str, Any]] = []
    for key in order:
        group = groups[key]
        first = group[0]
        confirmed = any(row.get("consent_status_key") == "confirmed" for row in group)
        materials_sent = any(
            _safe_text(row.get("materials_status")) == "sent" or _safe_text(row.get("materials_sent_at"))
            for row in group
        )
        best_status = min(
            (row.get("consent_status_key") or "pending" for row in group),
            key=_consent_status_rank,
        )
        representative = next(
            (row for row in group if row.get("consent_status_key") == "confirmed"), first
        )
        contacts = list(dict.fromkeys(_safe_text(row.get("contact")) for row in group if _safe_text(row.get("contact"))))
        interest_key = "high" if confirmed and materials_sent else ("medium" if confirmed else "low")
        companies.append(
            {
                **representative,
                "job_id": _safe_text(first.get("job_id")),
                "row_id": _safe_text(first.get("row_id")),
                "organization": _safe_text(first.get("organization")),
                "contact": ", ".join(contacts),
                "email": _safe_text(representative.get("email")),
                "consent_status_key": best_status,
                "consent_status_label": _safe_text(representative.get("consent_status_label")),
                "materials_status": "sent" if materials_sent else _safe_text(representative.get("materials_status")),
                "materials_label": _safe_text(representative.get("materials_label")),
                "last_action_label": _safe_text(representative.get("last_action_label")),
                "last_action_at": _safe_text(representative.get("last_action_at")),
                "interest": {
                    "key": interest_key,
                    "label": INTEREST_LABELS[interest_key],
                    "tone": INTEREST_TONES[interest_key],
                },
                "next_action": recommended_action_for("opened" if confirmed else "pending"),
                "email_count": len(group),
            }
        )
    return companies


def _load_company_consents_for_jobs(job_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    return _group_consents_into_companies(_load_consents_for_jobs(job_ids))


PROVIDER_FILTER_GROUPS: dict[str, frozenset[str]] = {
    "unisender": frozenset({"unisender", "unisender_go", "unisender_classic"}),
}


def _expand_provider_filters(providers: tuple[str, ...]) -> frozenset[str]:
    allowed: set[str] = set()
    for item in providers:
        normalized = _safe_text(item).lower()
        if not normalized:
            continue
        group = PROVIDER_FILTER_GROUPS.get(normalized)
        if group:
            allowed.update(group)
        else:
            allowed.add(normalized)
    return frozenset(allowed)


def _apply_recipient_filters(rows: list[dict[str, Any]], filters: StatsFilters) -> list[dict[str, Any]]:
    result = rows
    if filters.q:
        query = filters.q.lower()
        result = [
            row
            for row in result
            if query in _safe_text(row.get("organization")).lower()
            or query in _safe_text(row.get("recipient_name")).lower()
            or query in _safe_text(row.get("email")).lower()
            or query in _safe_text(row.get("emails_text")).lower()
        ]
    if filters.period_from or filters.period_to:
        result = [
            row
            for row in result
            if _within_period(row.get("sent_at_timestamp") or row.get("sent_at"), period_from=filters.period_from, period_to=filters.period_to)
        ]
    if filters.providers:
        allowed = _expand_provider_filters(filters.providers)
        result = [row for row in result if _safe_text(row.get("provider")).lower() in allowed]
    if filters.manager_statuses:
        allowed = set(filters.manager_statuses)
        result = [row for row in result if row.get("manager_status", {}).get("key") in allowed]
    if filters.recipient_roles:
        allowed = set(filters.recipient_roles)
        result = [row for row in result if row.get("role") in allowed]
    if filters.organization:
        query = filters.organization.lower()
        result = [row for row in result if query in _safe_text(row.get("organization")).lower()]
    if filters.problems_only:
        result = [row for row in result if row.get("manager_status", {}).get("category") == "problem"]
    if filters.quick_filter:
        quick = filters.quick_filter
        if quick == "delivered":
            result = [row for row in result if row.get("manager_status", {}).get("key") == "delivered"]
        elif quick == "opened":
            result = [row for row in result if row.get("manager_status", {}).get("key") == "opened"]
        elif quick == "clicked":
            result = [row for row in result if row.get("manager_status", {}).get("key") == "clicked"]
        elif quick == "problems":
            result = [row for row in result if row.get("manager_status", {}).get("category") == "problem"]
        elif quick == "pending":
            result = [row for row in result if row.get("manager_status", {}).get("key") in {"pending", "no_data"}]
        elif quick == "action":
            result = [row for row in result if row.get("next_action", {}).get("key") in {"call", "find_another_email", "manual_check", "retry_later"}]
    if filters.manager_action:
        result = [row for row in result if row.get("next_action", {}).get("key") == filters.manager_action]
    return result


CAMPAIGN_STATUS_LABELS: dict[str, str] = {
    "draft": "Черновик",
    "scheduled": "Запланирована",
    "running": "В работе",
    "paused": "На паузе",
    "completed": "Завершена",
    "completed_with_errors": "Завершена с ошибками",
    "cancelled": "Отменена",
}


def _load_campaign_statuses(job_ids: tuple[str, ...]) -> dict[str, str]:
    if not job_ids:
        return {}

    from sqlalchemy import select

    from src.infra.db import session_scope
    from src.infra.models import Campaign

    with session_scope() as session:
        rows = session.execute(
            select(Campaign.job_id, Campaign.status).where(Campaign.job_id.in_(job_ids))
        ).all()
    return {
        _safe_text(job_id): _safe_text(status).lower()
        for job_id, status in rows
        if _safe_text(job_id) and _safe_text(status).lower() in CAMPAIGN_STATUS_LABELS
    }


def _campaign_status(
    job_id: str,
    *,
    known_status: str = "",
    campaign_lookup_done: bool = False,
) -> str:
    # CampaignFlow keeps the authoritative lifecycle in the campaigns table.
    # Sender state is only a legacy fallback and often remains ``idle`` for
    # campaigns delivered by the batch worker, which previously made completed
    # campaigns appear as drafts in statistics.
    campaign_status = _safe_text(known_status).lower()
    if campaign_status in CAMPAIGN_STATUS_LABELS:
        return campaign_status

    if not campaign_lookup_done:
        from src.campaigns.service import get_campaign_by_job_id

        campaign = get_campaign_by_job_id(job_id)
        campaign_status = _safe_text((campaign or {}).get("status")).lower()
        if campaign_status in CAMPAIGN_STATUS_LABELS:
            return campaign_status

    sender_state = _load_sender_state(job_id)
    status = _safe_text(sender_state.get("status")) or "idle"
    mode = _safe_text(sender_state.get("mode")) or "dry_run"
    if status == "scheduled":
        return "scheduled"
    if status == "running":
        return "running"
    if status in {"completed", "stopped", "error"} and mode == "send":
        return "completed"
    return "draft"


def _campaign_period(job_id: str, rows: list[dict[str, Any]]) -> tuple[str, str]:
    sent_times = [_parse_datetime(row.get("sent_at_timestamp") or row.get("sent_at")) for row in rows if row.get("job_id") == job_id]
    sent_times = [item for item in sent_times if item is not None]
    if not sent_times:
        state = _load_sender_state(job_id)
        started = _safe_text(state.get("started_at"))
        completed = _safe_text(state.get("completed_at"))
        return started[:10], completed[:10] if completed else started[:10]
    start = min(sent_times).date().isoformat()
    end = max(sent_times).date().isoformat()
    return start, end


def _campaign_period_label(period_from: str, period_to: str) -> str:
    def _format(value: str) -> str:
        try:
            return datetime.fromisoformat(value).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            return value

    if not period_from and not period_to:
        return ""
    if not period_to or period_from == period_to:
        return _format(period_from or period_to)
    return f"{_format(period_from)} — {_format(period_to)}"


def _aggregate_counts(rows: list[dict[str, Any]], consent_rows: list[dict[str, Any]]) -> dict[str, int]:
    manager_keys = Counter(row.get("manager_status", {}).get("key") for row in rows)
    confirmed = sum(1 for row in consent_rows if row.get("consent_status_key") == "confirmed")
    materials_sent = sum(
        1
        for row in consent_rows
        if row.get("materials_status") == "sent" or _safe_text(row.get("materials_sent_at"))
    )
    delivered = manager_keys.get("delivered", 0) + manager_keys.get("opened", 0) + manager_keys.get("clicked", 0)
    opened = manager_keys.get("opened", 0) + manager_keys.get("clicked", 0)
    clicked = manager_keys.get("clicked", 0)
    provider_errors = sum(
        manager_keys.get(key, 0)
        for key in ("email_broken", "soft_bounce", "delivery_error")
    )
    errors = provider_errors + manager_keys.get("spam", 0)
    pending = manager_keys.get("pending", 0) + manager_keys.get("no_data", 0)
    layout_errors = sum(1 for row in rows if _safe_text(row.get("layout_error_code")) == "kp_font_compact")
    return {
        "sent": len(rows),
        "delivered": delivered,
        "opened": opened,
        "clicked": clicked,
        "errors": errors,
        "provider_errors": provider_errors,
        "layout_errors": layout_errors,
        "pending": pending,
        "consents": confirmed,
        "materials_sent": materials_sent,
        "unsubscribed": manager_keys.get("unsubscribed", 0),
        "spam": manager_keys.get("spam", 0),
    }


def build_funnels(*, counts: dict[str, int]) -> list[dict[str, Any]]:
    consent = counts.get("consents", 0)
    sent = counts.get("sent", 0)
    delivered = counts.get("delivered", 0)
    opened = counts.get("opened", 0)
    clicked = counts.get("clicked", 0)
    total_attempts = max(0, int(counts.get("total_attempts", 0)))
    steps = [
        ("consent", "Согласие", consent),
        ("sent", "Принято провайдером", sent),
        ("delivered", "Доставлено", delivered),
        ("opened", "Открыто", opened),
        ("clicked", "Переходы", clicked),
    ]
    base = total_attempts or sent or consent or 1
    base_label = (
        "всех попыток отправки"
        if total_attempts
        else "принятых провайдером писем"
        if sent
        else "базы воронки"
    )
    return [
        {
            "id": step_id,
            "label": label,
            "value": value,
            "percent": _pct(value, base),
            "base": base,
            "base_label": base_label,
        }
        for step_id, label, value in steps
    ]


def build_work_lists(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    interested: dict[str, int] = Counter()
    problems: dict[str, int] = Counter()
    callbacks: dict[str, int] = Counter()
    for row in rows:
        org = _safe_text(row.get("organization")) or "Без названия"
        status_key = row.get("manager_status", {}).get("key")
        if status_key in {"opened", "clicked"}:
            interested[org] += 1
        if status_key in {"email_broken", "soft_bounce", "delivery_error"}:
            problems[org] += 1
        if row.get("next_action", {}).get("key") == "call":
            callbacks[org] += 1

    def _top(counter: Counter[str], limit: int = 5) -> list[dict[str, Any]]:
        return [{"organization": org, "count": count} for org, count in counter.most_common(limit)]

    return {
        "interested": _top(interested),
        "email_problems": _top(problems),
        "need_call": _top(callbacks),
    }


def build_insights(*, rows: list[dict[str, Any]], counts: dict[str, int]) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    provider_delivered: Counter[str] = Counter()
    provider_total: Counter[str] = Counter()
    role_opened: Counter[str] = Counter()
    role_total: Counter[str] = Counter()
    for row in rows:
        provider = _safe_text(row.get("provider")) or "unknown"
        provider_total[provider] += 1
        if row.get("manager_status", {}).get("key") in {"delivered", "opened", "clicked"}:
            provider_delivered[provider] += 1
        role = row.get("role") or "unknown"
        role_total[role] += 1
        if row.get("manager_status", {}).get("key") in {"opened", "clicked"}:
            role_opened[role] += 1

    if provider_total:
        best_provider = max(
            provider_total,
            key=lambda name: _pct(provider_delivered.get(name, 0), provider_total.get(name, 0)),
        )
        insights.append(
            {
                "id": "best_provider",
                "title": "Лучший провайдер",
                "text": f"{_provider_label(best_provider)} показывает лучшую доставляемость.",
            }
        )
    primary_open = _pct(role_opened.get("primary", 0), role_total.get("primary", 0))
    fallback_open = _pct(role_opened.get("fallback", 0), role_total.get("fallback", 0))
    if primary_open > fallback_open:
        insights.append(
            {
                "id": "primary_email",
                "title": "Основной email",
                "text": "Чаще открывают на основной email.",
            }
        )
    problem_count = sum(1 for row in rows if row.get("manager_status", {}).get("category") == "problem")
    if problem_count:
        insights.append(
            {
                "id": "problem_addresses",
                "title": "Проблемные адреса",
                "text": f"Требуется проверить {problem_count} адресов.",
            }
        )
    if not insights:
        insights.append(
            {
                "id": "empty",
                "title": "Нет данных",
                "text": "Статистика появится после реальной отправки писем.",
            }
        )
    return insights


def build_manager_dashboard(filters: StatsFilters, *, refresh: bool = False) -> dict[str, Any]:
    # Always serve cached rows instantly; provider dooбор happens in the
    # background so the request never blocks on remote provider APIs. Rows are
    # aggregated per company (row_id), not per email.
    all_rows = _load_companies_for_jobs(filters.job_ids)
    rows_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        rows_by_job[_safe_text(row.get("job_id"))].append(row)
    refresh_started, refresh_in_progress = _trigger_provider_refresh(
        filters.job_ids,
        rows_by_job,
        manual=refresh,
        auto=True,
    )
    rows = all_rows
    consent_rows = _load_company_consents_for_jobs(filters.job_ids)
    rows = _apply_recipient_filters(rows, filters)
    consent_rows = [
        row
        for row in consent_rows
        if not filters.period_from and not filters.period_to
        or _within_period(row.get("last_action_at"), period_from=filters.period_from, period_to=filters.period_to)
    ]
    counts = _aggregate_counts(rows, consent_rows)
    total = counts["sent"]
    rates = {
        "delivery_rate": _pct(counts["delivered"], total),
        "open_rate": _pct(counts["opened"], counts["delivered"] or total),
        "ctr": _pct(counts["clicked"], total),
        "error_rate": _pct(counts["errors"], total),
        "pending_rate": _pct(counts["pending"], total),
    }
    cards = [
        {"id": "sent", "title": "Компаний в рассылке", "value": counts["sent"], "tone": "neutral"},
        {"id": "delivered", "title": "Доставлено", "value": counts["delivered"], "tone": "good"},
        {"id": "opened", "title": "Открыто", "value": counts["opened"], "tone": "good"},
        {"id": "clicked", "title": "Переходы", "value": counts["clicked"], "tone": "good"},
        {"id": "errors", "title": "Ошибки", "value": counts["errors"], "tone": "bad" if counts["errors"] else "neutral"},
        {"id": "pending", "title": "Ожидают статуса", "value": counts["pending"], "tone": "warn" if counts["pending"] else "neutral"},
        {"id": "consents", "title": "Согласия", "value": counts["consents"], "tone": "good" if counts["consents"] else "neutral"},
        {"id": "materials_sent", "title": "Материалы отправлены", "value": counts["materials_sent"], "tone": "good" if counts["materials_sent"] else "neutral"},
    ]
    statuses = Counter(row.get("manager_status", {}).get("key") for row in rows)
    providers = Counter(_safe_text(row.get("provider")) or "unknown" for row in rows)
    roles = Counter(row.get("role") or "unknown" for row in rows)
    generated_at = _now_moscow()
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "generated_at_label": _format_moscow_datetime(generated_at),
        "cards": cards,
        "rates": rates,
        "summary": counts,
        "statuses": [
            {
                "status": key,
                "label": MANAGER_STATUS_DEFINITIONS[key]["label"],
                "count": statuses.get(key, 0),
                "tone": MANAGER_STATUS_DEFINITIONS[key]["tone"],
            }
            for key in MANAGER_STATUS_DEFINITIONS
            if statuses.get(key, 0)
        ],
        "providers": [
            {"provider": provider, "label": _provider_label(provider), "count": count}
            for provider, count in providers.most_common()
        ],
        "roles": [
            {"role": role, "label": RECIPIENT_ROLE_LABELS.get(role, role), "count": count}
            for role, count in roles.most_common()
        ],
        "funnels": build_funnels(counts=counts),
        "work_lists": build_work_lists(rows),
        "insights": build_insights(rows=rows, counts=counts),
        "total": total,
        "empty": total <= 0,
        "refresh_started": refresh_started,
        "refresh_in_progress": refresh_in_progress,
        "awaiting_provider_events": bool(total > 0 and counts["pending"] >= total),
    }


def build_campaigns(filters: StatsFilters) -> dict[str, Any]:
    campaigns: list[dict[str, Any]] = []
    totals = {
        "total": 0,
        "active": 0,
        "running": 0,
        "paused": 0,
        "completed": 0,
        "completed_with_errors": 0,
        "cancelled": 0,
        "draft": 0,
        "scheduled": 0,
    }
    delivery_rates: list[float] = []
    open_rates: list[float] = []
    # Load and filter everything once, then group by job in memory instead of
    # re-reading each job (DB + provider files) inside the loop.
    # ``q`` on this endpoint searches campaign titles, not recipient/company
    # fields. Applying it to delivery rows used to zero otherwise matching
    # campaigns and made the table look inconsistent with the campaign picker.
    row_filters = StatsFilters(
        job_ids=filters.job_ids,
        period_from=filters.period_from,
        period_to=filters.period_to,
        providers=filters.providers,
    )
    all_rows = _apply_recipient_filters(_load_companies_for_jobs(filters.job_ids), row_filters)
    all_consents = _load_company_consents_for_jobs(filters.job_ids)
    rows_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        rows_by_job[_safe_text(row.get("job_id"))].append(row)
    consents_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_consents:
        consents_by_job[_safe_text(row.get("job_id"))].append(row)
    campaign_statuses = _load_campaign_statuses(filters.job_ids)
    for job_id in filters.job_ids:
        rows = rows_by_job.get(job_id, [])
        consent_rows = consents_by_job.get(job_id, [])
        campaign = _campaign_metadata(job_id, rows=rows, consent_rows=consent_rows)
        if filters.q and filters.q.casefold() not in _safe_text(campaign["title"]).casefold():
            continue
        # Period/provider filters describe actual sends. A campaign without a
        # matching delivery row must not remain in the filtered table as a row
        # full of zeroes.
        if (filters.period_from or filters.period_to or filters.providers) and not rows:
            continue

        counts = _aggregate_counts(rows, consent_rows)
        status = _campaign_status(
            job_id,
            known_status=campaign_statuses.get(job_id, ""),
            campaign_lookup_done=True,
        )
        totals["total"] += 1
        if status in totals:
            totals[status] += 1
        if status in {"running", "paused"}:
            totals["active"] += 1
        if status == "completed_with_errors":
            totals["completed"] += 1
        delivery_rate = _pct(counts["delivered"], counts["sent"])
        open_rate = _pct(counts["opened"], counts["delivered"] or counts["sent"])
        if counts["sent"]:
            delivery_rates.append(delivery_rate)
            open_rates.append(open_rate)
        period_from, period_to = _campaign_period(job_id, rows)
        provider_counter = Counter(_safe_text(row.get("provider")) or "unknown" for row in rows)
        provider_key = provider_counter.most_common(1)[0][0] if provider_counter else ""
        campaign_id = next(
            (_safe_text(row.get("campaign_id")) for row in rows if _safe_text(row.get("campaign_id"))),
            "",
        )
        campaigns.append(
            {
                "job_id": job_id,
                "campaign_id": campaign_id,
                "can_delete": bool(campaign_id) and status in {"completed", "draft"},
                "title": campaign["title"],
                "period_from": period_from,
                "period_to": period_to,
                "period_label": _campaign_period_label(period_from, period_to),
                "provider": provider_key,
                "provider_label": _provider_label(provider_key),
                "sent": counts["sent"],
                "delivered": counts["delivered"],
                "opened": counts["opened"],
                "clicked": counts["clicked"],
                "consents": counts["consents"],
                "delivery_rate": delivery_rate,
                "open_rate": open_rate,
                "ctr": _pct(counts["clicked"], counts["sent"]),
                "status": status,
                "status_label": CAMPAIGN_STATUS_LABELS.get(status, status or "—"),
            }
        )
    campaigns.sort(key=lambda item: item.get("period_to") or "", reverse=True)
    return {
        "campaigns": campaigns,
        "summary": {
            **totals,
            "avg_delivery_rate": round(sum(delivery_rates) / len(delivery_rates), 1) if delivery_rates else 0.0,
            "avg_open_rate": round(sum(open_rates) / len(open_rates), 1) if open_rates else 0.0,
        },
    }


def _company_item(row: dict[str, Any]) -> dict[str, Any]:
    """Public per-company payload consumed by the recipients/companies views."""
    return {
        "row_key": row.get("row_key"),
        "job_id": row.get("job_id"),
        "campaign_id": row.get("campaign_id"),
        "row_id": row.get("row_id"),
        "can_delete": bool(row.get("campaign_id") and row.get("row_id")),
        "organization": row.get("organization"),
        "recipient_name": row.get("recipient_name"),
        "email": row.get("email"),
        "emails": row.get("emails", []),
        "email_count": row.get("email_count", len(row.get("emails", []))),
        "role_label": row.get("role_label"),
        "company": row.get("company", {}),
        "manager_status": row.get("manager_status"),
        "last_event_at": row.get("last_event_at"),
        "last_event_label": row.get("last_event_label"),
        "interest": row.get("interest"),
        "next_action": row.get("next_action"),
    }


def build_recipients(filters: StatsFilters, *, page: int = 1, per_page: int = 10) -> dict[str, Any]:
    rows = _apply_recipient_filters(_load_companies_for_jobs(filters.job_ids), filters)
    total = len(rows)
    start = max(0, (page - 1) * per_page)
    end = start + per_page
    page_rows = rows[start:end]
    summary = {
        "total": total,
        "active": sum(1 for row in rows if row.get("manager_status", {}).get("key") in {"opened", "clicked"}),
        "problematic": sum(1 for row in rows if row.get("manager_status", {}).get("category") == "problem"),
        "need_call": sum(1 for row in rows if row.get("next_action", {}).get("key") == "call"),
    }
    return {
        "items": [_company_item(row) for row in page_rows],
        "summary": summary,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": max(1, (total + per_page - 1) // per_page),
        },
    }


ATTEMPT_STATUS_LABELS: dict[str, str] = {
    "queued": "В очереди",
    "pending": "Ожидает отправки",
    "processing": "Отправляется",
    "sent": "Принято провайдером",
    "accepted": "Принято провайдером",
    "success": "Принято провайдером",
    "delivered": "Доставлено",
    "opened": "Открыто",
    "clicked": "Переход по ссылке",
    "failed": "Ошибка",
    "error": "Ошибка",
    "rejected": "Отклонено",
    "skipped": "Пропущено",
    "cancelled": "Отменено",
}


def _attempt_status_label(status: Any) -> str:
    key = _safe_text(status).strip().lower()
    return ATTEMPT_STATUS_LABELS.get(key, key.replace("_", " ").strip().capitalize() or "Нет статуса")


def _attempt_has_send_error(item: dict[str, Any]) -> bool:
    status = _safe_text(item.get("status")).lower()
    explicit_failure_status = bool(
        status in {"failed", "error", "rejected", "skipped", "cancelled"}
        or "error" in status
        or "fail" in status
        or "reject" in status
    )
    return bool(
        explicit_failure_status
        or (
            _safe_text(item.get("error"))
            and not _safe_text(item.get("provider_message_id"))
        )
    )


def _attempt_manager_status_key(item: dict[str, Any]) -> str:
    manager_status = item.get("manager_status")
    if isinstance(manager_status, dict):
        status_key = _safe_text(manager_status.get("key")).lower()
    else:
        status_key = ""
    if not status_key:
        status_key = _safe_text(normalize_manager_status(item.get("status")).get("key")).lower()
    return status_key


def _attempt_has_delivery_error(item: dict[str, Any]) -> bool:
    return _attempt_manager_status_key(item) in {
        "email_broken",
        "soft_bounce",
        "delivery_error",
        "spam",
    }


def _delivery_failure_error_label(
    delivery: dict[str, Any],
    manager_status: dict[str, Any],
) -> str:
    bounce_reason = _safe_text(delivery.get("bounce_reason")).lower()
    bounce_reason_label = _safe_text(delivery.get("bounce_reason_label"))
    delivery_response = _safe_text(delivery.get("delivery_response"))
    if bounce_reason_label and bounce_reason != "other":
        error_label = bounce_reason_label
    else:
        error_label = _safe_text(manager_status.get("label")) or bounce_reason_label
    if delivery_response:
        return f"{error_label}: {delivery_response}" if error_label else delivery_response
    return error_label


def _attempt_is_error(item: dict[str, Any]) -> bool:
    return _attempt_has_send_error(item) or _attempt_has_delivery_error(item)


def _attempt_is_sent(item: dict[str, Any]) -> bool:
    if _attempt_has_send_error(item):
        return False
    status = _safe_text(item.get("status")).lower()
    return bool(
        _safe_text(item.get("provider_message_id"))
        or status
        in {
            "sent",
            "accepted",
            "success",
            "delivered",
            "opened",
            "clicked",
            "ok_sent",
            "ok_delivered",
            "ok_read",
            "ok_link_visited",
        }
    )


def _attempt_is_delivered(item: dict[str, Any]) -> bool:
    return _attempt_manager_status_key(item) in {"delivered", "opened", "clicked"}


def _attempt_is_pending(item: dict[str, Any]) -> bool:
    return _attempt_manager_status_key(item) in {"pending", "no_data"}


def _load_campaign_delivery_attempts(job_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Load every canonical CampaignFlow attempt for one mailing in one query."""
    from sqlalchemy import select

    from src.infra.db import session_scope
    from src.infra.models import Campaign, CampaignRecipient, DeliveryAttempt

    with session_scope() as session:
        campaign = session.scalar(select(Campaign).where(Campaign.job_id == job_id).limit(1))
        if campaign is None:
            return "", []
        rows = session.execute(
            select(DeliveryAttempt, CampaignRecipient)
            .join(CampaignRecipient, CampaignRecipient.id == DeliveryAttempt.recipient_id)
            .where(DeliveryAttempt.campaign_id == campaign.id)
            .order_by(DeliveryAttempt.created_at.desc(), DeliveryAttempt.id.desc())
        ).all()
        return (
            _safe_text(campaign.id),
            [
                {
                    "id": attempt.id,
                    "campaign_id": _safe_text(campaign.id),
                    "recipient_id": attempt.recipient_id,
                    "row_id": _safe_text(attempt.recipient_id),
                    "batch_id": attempt.batch_id,
                    "attempt_number": attempt.attempt_number,
                    "status": attempt.status,
                    "delivery_email": attempt.delivery_email,
                    "provider_message_id": attempt.provider_message_id,
                    "error": attempt.error,
                    "organization": recipient.company,
                    "contact_name": recipient.contact_name,
                    "email": recipient.email,
                    "created_at": attempt.created_at.isoformat() if attempt.created_at else "",
                    "updated_at": attempt.updated_at.isoformat() if attempt.updated_at else "",
                }
                for attempt, recipient in rows
            ],
        )


def _sent_log_message_id(item: dict[str, Any]) -> str:
    provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
    return _safe_text(item.get("provider_message_id") or provider.get("message_id"))


def _sent_log_attempt_row(
    job_id: str,
    index: int,
    item: dict[str, Any],
    delivery_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    row_id = _safe_text(item.get("row_id") or item.get("recipient_id"))
    email = _safe_text(item.get("email") or item.get("recipient")).lower()
    delivery = delivery_index.get((row_id, email), {})
    status = _safe_text(item.get("status")) or "sent"
    provider = _safe_text(item.get("transport") or delivery.get("provider"))
    manager_status = delivery.get("manager_status") or normalize_manager_status(status)
    error = _safe_text(item.get("error") or delivery.get("error"))
    if not error and _attempt_has_delivery_error(
        {"status": status, "manager_status": manager_status}
    ):
        error = _delivery_failure_error_label(delivery, manager_status)
    return {
        "id": f"log-{index + 1}",
        "job_id": job_id,
        "campaign_id": _safe_text(item.get("campaign_id")),
        "recipient_id": item.get("recipient_id"),
        "row_id": row_id,
        "attempt_number": int(item.get("attempt_number") or 1),
        "status": status,
        "status_label": _attempt_status_label(status),
        "manager_status": manager_status,
        "delivery_status_label": _safe_text(manager_status.get("label")),
        "email": email,
        "organization": _safe_text(item.get("organization") or item.get("mun_name"))
        or _safe_text(delivery.get("organization")),
        "subject": _safe_text(item.get("subject") or delivery.get("subject")),
        "provider": provider,
        "provider_label": _provider_label(provider),
        "provider_message_id": _sent_log_message_id(item),
        "error": error,
        "created_at": _safe_text(item.get("sent_at") or delivery.get("sent_at")),
        "updated_at": _safe_text(delivery.get("last_event_at") or item.get("sent_at")),
        "attachments": list(item.get("attachments") or []),
    }


def _unmatched_sent_log_indexes(
    database_attempts: list[dict[str, Any]],
    sent_log: list[dict[str, Any]],
) -> list[int]:
    """Return real sends absent from DeliveryAttempt using exact IDs first."""
    attempt_message_ids = {
        _safe_text(item.get("provider_message_id"))
        for item in database_attempts
        if _safe_text(item.get("provider_message_id"))
    }
    anonymous_successes = Counter(
        (
            _safe_text(item.get("row_id") or item.get("recipient_id")),
            _safe_text(item.get("delivery_email") or item.get("email")).lower(),
        )
        for item in database_attempts
        if not _safe_text(item.get("provider_message_id")) and _attempt_is_sent(item)
    )
    unmatched: list[int] = []
    for index, item in enumerate(sent_log):
        message_id = _sent_log_message_id(item)
        if message_id:
            if message_id not in attempt_message_ids:
                unmatched.append(index)
            continue
        key = (
            _safe_text(item.get("row_id") or item.get("recipient_id")),
            _safe_text(item.get("email") or item.get("recipient")).lower(),
        )
        if anonymous_successes[key] > 0:
            anonymous_successes[key] -= 1
        else:
            unmatched.append(index)
    return unmatched


def _campaign_attempt_rows(job_id: str) -> list[dict[str, Any]]:
    """Return canonical attempts, using the sent-mail log only for legacy jobs."""
    from src.jobs.job_docs import read_sent_mail_log

    sent_log = list(read_sent_mail_log(job_id))
    try:
        campaign_id, database_attempts = _load_campaign_delivery_attempts(job_id)
    except Exception as exc:  # pragma: no cover - legacy installations may not have CampaignFlow tables
        logger.warning("campaign_attempts_load_failed", job_id=job_id, error=str(exc))
        campaign_id, database_attempts = "", []

    delivery_rows = _load_delivery_for_jobs((job_id,))
    delivery_index = {
        (
            _safe_text(row.get("row_id")),
            _safe_text(row.get("email") or row.get("recipient")).lower(),
        ): row
        for row in delivery_rows
    }
    log_by_message_id = {
        message_id: item
        for item in sent_log
        if (message_id := _sent_log_message_id(item))
    }
    log_by_recipient: dict[tuple[str, str], dict[str, Any]] = {}
    for item in sent_log:
        row_id = _safe_text(item.get("row_id") or item.get("recipient_id"))
        email = _safe_text(item.get("email") or item.get("recipient")).lower()
        log_by_recipient.setdefault((row_id, email), item)

    if database_attempts:
        attempts: list[dict[str, Any]] = []
        for item in database_attempts:
            row_id = _safe_text(item.get("row_id") or item.get("recipient_id"))
            email = _safe_text(item.get("delivery_email") or item.get("email")).lower()
            provider_message_id = _safe_text(item.get("provider_message_id"))
            sent = log_by_message_id.get(provider_message_id) if provider_message_id else None
            if not sent and _attempt_is_sent(item):
                sent = log_by_recipient.get((row_id, email))
            sent = sent or {}
            delivery = delivery_index.get((row_id, email), {})
            provider = _safe_text(sent.get("transport") or delivery.get("provider"))
            status = _safe_text(item.get("status")) or "pending"
            manager_status = delivery.get("manager_status") or normalize_manager_status(status)
            error = _safe_text(
                item.get("error")
                or sent.get("error")
                or delivery.get("error")
            )
            if not error and _attempt_has_delivery_error(
                {"status": status, "manager_status": manager_status}
            ):
                error = _delivery_failure_error_label(delivery, manager_status)
            attempts.append(
                {
                    **item,
                    "job_id": job_id,
                    "campaign_id": campaign_id,
                    "row_id": row_id,
                    "email": email,
                    "organization": _safe_text(item.get("organization"))
                    or _safe_text(delivery.get("organization")),
                    "subject": _safe_text(sent.get("subject") or delivery.get("subject")),
                    "status": status,
                    "status_label": _attempt_status_label(status),
                    "manager_status": manager_status,
                    "delivery_status_label": _safe_text(manager_status.get("label")),
                    "provider": provider,
                    "provider_label": _provider_label(provider),
                    "provider_message_id": provider_message_id
                    or _sent_log_message_id(sent),
                    "error": error,
                    "attachments": list(sent.get("attachments") or []),
                }
            )
        return attempts

    # Legacy jobs have no DeliveryAttempt records. Only in that case each
    # sent-log record is the best available canonical attempt source. A current
    # CampaignFlow delivery attempt can legitimately create multiple sent-log
    # rows (for example for primary and fallback addresses), so the sources must
    # never be added together.
    return [
        _sent_log_attempt_row(job_id, index, item, delivery_index)
        for index, item in enumerate(sent_log)
    ]


def _campaign_attempt_total(
    job_id: str,
    *,
    period_from: str = "",
    period_to: str = "",
) -> int:
    """Count canonical attempts, falling back to real sends for legacy jobs."""
    if period_from or period_to:
        return len(
            _filter_rows_by_period(
                _campaign_attempt_rows(job_id),
                period_from=period_from,
                period_to=period_to,
                timestamp_fields=("created_at", "sent_at_timestamp", "sent_at", "updated_at"),
            )
        )

    from src.jobs.job_docs import read_sent_mail_log

    try:
        _campaign_id, database_attempts = _load_campaign_delivery_attempts(job_id)
        if database_attempts:
            return len(database_attempts)
    except Exception as exc:  # pragma: no cover - defensive legacy fallback
        logger.warning("campaign_attempt_count_failed", job_id=job_id, error=str(exc))
    return len(read_sent_mail_log(job_id))


def build_campaign_attempts(
    job_id: str,
    *,
    page: int = 1,
    per_page: int = 100,
    period_from: str = "",
    period_to: str = "",
) -> dict[str, Any]:
    """Group every attempt/send in one mailing by company for the standard drilldown."""
    attempts = _campaign_attempt_rows(job_id)
    attempts = _filter_rows_by_period(
        attempts,
        period_from=period_from,
        period_to=period_to,
        timestamp_fields=("created_at", "sent_at_timestamp", "sent_at", "updated_at"),
    )

    companies = {
        _safe_text(row.get("row_id")): row
        for row in _load_companies_for_jobs((job_id,))
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in attempts:
        row_id = _safe_text(item.get("row_id"))
        key = row_id or _safe_text(item.get("email")) or _safe_text(item.get("id"))
        grouped[key].append(item)

    items: list[dict[str, Any]] = []
    for key, group in grouped.items():
        row_id = _safe_text(group[0].get("row_id"))
        company = companies.get(row_id, {})
        emails = list(
            dict.fromkeys(
                _safe_text(item.get("email")).lower()
                for item in group
                if _safe_text(item.get("email"))
            )
        )
        status_counts = Counter(_safe_text(item.get("status_label")) for item in group)
        provider_labels = list(
            dict.fromkeys(
                _safe_text(item.get("provider_label"))
                for item in group
                if _safe_text(item.get("provider_label"))
            )
        )
        sent_count = sum(1 for item in group if _attempt_is_sent(item))
        manager_status = (
            company.get("manager_status")
            or group[0].get("manager_status")
            or {}
        )
        manager_status_key = _safe_text(manager_status.get("key"))
        delivered_count = int(
            manager_status_key in {"delivered", "opened", "clicked"}
            or (
                not manager_status_key
                and any(_attempt_is_delivered(item) for item in group)
            )
        )
        send_error_count = sum(
            1 for item in group if _attempt_has_send_error(item)
        )
        provider_error_count = int(
            (
                bool(company)
                and manager_status_key
                in {"email_broken", "soft_bounce", "delivery_error"}
            )
            or (
                not company
                and any(
                    _attempt_has_delivery_error(item)
                    and not _attempt_has_send_error(item)
                    for item in group
                )
            )
        )
        error_count = send_error_count + provider_error_count
        pending_count = sum(1 for item in group if _attempt_is_pending(item))
        last_event_at = max(
            (
                _safe_text(item.get("updated_at") or item.get("created_at"))
                for item in group
            ),
            default="",
        )
        primary_email = emails[0] if emails else ""
        items.append(
            {
                "row_key": _safe_text(company.get("row_key"))
                or make_row_key(job_id, row_id or key, primary_email),
                "job_id": job_id,
                "row_id": row_id,
                "organization": _safe_text(company.get("organization"))
                or _safe_text(group[0].get("organization"))
                or (f"Компания №{row_id}" if row_id else "Без названия"),
                "company": company.get("company", {}),
                "email": primary_email,
                "emails": [{"email": email} for email in emails],
                "email_count": len(emails),
                "attempts_total": len(group),
                "sent_count": sent_count,
                "delivered_count": delivered_count,
                "error_count": error_count,
                "send_error_count": send_error_count,
                "provider_error_count": provider_error_count,
                "pending_count": pending_count,
                "status_counts": dict(status_counts),
                "status_summary": " · ".join(
                    f"{label}: {count}" for label, count in status_counts.items()
                ),
                "provider_labels": provider_labels,
                "last_event_at": last_event_at,
                "manager_status": manager_status,
            }
        )
    items.sort(
        key=lambda item: (
            _safe_text(item.get("last_event_at")),
            _safe_text(item.get("organization")).casefold(),
        ),
        reverse=True,
    )
    page_items, pagination = _paginate_list(items, page=page, per_page=per_page)
    return {
        "items": page_items,
        "summary": {
            "total_attempts": len(attempts),
            "companies": len(items),
            "sent": sum(1 for item in attempts if _attempt_is_sent(item)),
            "accepted_recipients": sum(
                1 for item in items if int(item.get("sent_count") or 0) > 0
            ),
            "delivered": sum(
                int(item.get("delivered_count") or 0) for item in items
            ),
            "errors": sum(int(item.get("error_count") or 0) for item in items),
            "send_errors": sum(
                int(item.get("send_error_count") or 0) for item in items
            ),
            "provider_errors": sum(
                int(item.get("provider_error_count") or 0) for item in items
            ),
            "pending": sum(1 for item in attempts if _attempt_is_pending(item)),
        },
        "pagination": pagination,
    }


def _company_documents(rows: list[dict[str, Any]], *, limit_per_job: int = 200) -> list[dict[str, Any]]:
    from src.generator.generation.document_builder import read_output_folder_manifest
    from src.web.download_sources import archive_entry_label, downloadable_output_files

    documents: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        job_id = _safe_text(row.get("job_id"))
        row_id = _safe_text(row.get("row_id"))
        organization = _safe_text(row.get("organization"))
        output_dir = resolve_job_paths(job_id).output_dir
        if not output_dir.exists():
            continue
        matched_count = 0
        normalized_organization = organization.casefold()
        for file_path in sorted(
            downloadable_output_files(output_dir),
            key=lambda path: str(path.relative_to(output_dir)).casefold(),
        ):
            relative_path = file_path.relative_to(output_dir)
            manifest_folder = (
                output_dir / relative_path.parts[0]
                if len(relative_path.parts) > 1
                else file_path.parent
            )
            manifest = read_output_folder_manifest(manifest_folder)
            manifest_row_id = _safe_text(manifest.get("row_id"))
            manifest_organization = _safe_text(manifest.get("mun_name")).casefold()
            flat_name_matches = bool(
                row_id and re.match(rf"^{re.escape(row_id)}_(?:contract|kp)_", file_path.name, re.IGNORECASE)
            )
            if not (
                (row_id and manifest_row_id == row_id)
                or flat_name_matches
                or (
                    normalized_organization
                    and manifest_organization
                    and manifest_organization == normalized_organization
                )
            ):
                continue
            relative = str(relative_path).replace("\\", "/")
            key = (job_id, relative)
            if key in seen:
                continue
            seen.add(key)
            documents.append(
                {
                    "path": relative,
                    "name": file_path.name,
                    "ext": file_path.suffix.lower(),
                    "size": file_path.stat().st_size,
                    "label": archive_entry_label(output_dir, file_path),
                    "job_id": job_id,
                    "campaign_title": _safe_text(row.get("campaign_name")) or job_id,
                    "row_id": row_id,
                }
            )
            matched_count += 1
            if matched_count >= limit_per_job:
                break
    return documents


def _company_sent_emails(
    rows: list[dict[str, Any]],
    filters: StatsFilters,
) -> list[dict[str, Any]]:
    from src.jobs.job_docs import read_sent_mail_log

    row_keys = {
        (_safe_text(row.get("job_id")), _safe_text(row.get("row_id")))
        for row in rows
    }
    delivery_rows = _apply_recipient_filters(_load_delivery_for_jobs(filters.job_ids), filters)
    delivery_index = {
        (
            _safe_text(row.get("job_id")),
            _safe_text(row.get("row_id")),
            _safe_text(row.get("email") or row.get("recipient")).lower(),
        ): row
        for row in delivery_rows
    }
    events: list[dict[str, Any]] = []
    for job_id in dict.fromkeys(job_id for job_id, _ in row_keys):
        for item in read_sent_mail_log(job_id):
            row_id = _safe_text(item.get("row_id") or item.get("recipient_id"))
            if (job_id, row_id) not in row_keys:
                continue
            sent_at = _safe_text(item.get("sent_at"))
            if (filters.period_from or filters.period_to) and not _within_period(
                sent_at,
                period_from=filters.period_from,
                period_to=filters.period_to,
            ):
                continue
            email = _safe_text(item.get("email") or item.get("recipient")).lower()
            delivery = delivery_index.get((job_id, row_id, email), {})
            transport = _safe_text(item.get("transport") or delivery.get("provider"))
            provider_payload = item.get("provider") if isinstance(item.get("provider"), dict) else {}
            campaign_id = _safe_text(item.get("campaign_id"))
            recipient_id = _safe_text(item.get("recipient_id") or row_id)
            manager_status = delivery.get("manager_status") or normalize_manager_status(
                item.get("status") or "sent"
            )
            error = _safe_text(delivery.get("error"))
            if not error and _attempt_has_delivery_error(
                {"status": item.get("status") or "sent", "manager_status": manager_status}
            ):
                error = _delivery_failure_error_label(delivery, manager_status)
            events.append(
                {
                    "row_key": _safe_text(delivery.get("row_key"))
                    or make_row_key(job_id, row_id, email),
                    "job_id": job_id,
                    "row_id": row_id,
                    "campaign_title": _safe_text(item.get("campaign_name"))
                    or _safe_text(delivery.get("campaign_name"))
                    or job_id,
                    "campaign_id": campaign_id,
                    "recipient_id": int(recipient_id) if recipient_id.isdigit() else None,
                    "preview_available": bool(campaign_id and recipient_id.isdigit()),
                    "email": email,
                    "role_label": RECIPIENT_ROLE_LABELS.get(
                        _normalize_recipient_role(
                            item.get("recipient_role") or delivery.get("recipient_role")
                        ),
                        RECIPIENT_ROLE_LABELS["unknown"],
                    ),
                    "subject": _safe_text(item.get("subject") or delivery.get("subject")),
                    "sent_at": sent_at,
                    "last_event_at": _safe_text(delivery.get("last_event_at")) or sent_at,
                    "provider": transport,
                    "provider_label": _provider_label(transport),
                    "manager_status": manager_status,
                    "bounce_reason_label": _safe_text(delivery.get("bounce_reason_label")),
                    "error": error,
                    "attachments": list(item.get("attachments") or []),
                    "provider_message_id": _safe_text(
                        item.get("provider_message_id")
                        or provider_payload.get("message_id")
                    ),
                }
            )
    events.sort(
        key=lambda item: _safe_text(item.get("sent_at") or item.get("last_event_at")),
        reverse=True,
    )
    return events


def build_recipient_detail(row_key: str) -> dict[str, Any] | None:
    job_id, row_id, _email = parse_row_key(row_key)
    companies = _load_companies_for_jobs((job_id,))
    matched = next(
        (row for row in companies if row.get("row_key") == row_key or _safe_text(row.get("row_id")) == row_id),
        None,
    )
    if matched is None:
        return None
    # One status per email so the card can show the whole company at a glance.
    status_history = [
        {
            "label": f"{item.get('email', '')} · {item.get('manager_status', {}).get('label', '')}".strip(" ·"),
            "at": item.get("last_event_at", ""),
            "tone": item.get("manager_status", {}).get("tone", "neutral"),
        }
        for item in matched.get("emails", [])
    ] or [
        {
            "label": matched["manager_status"]["label"],
            "at": matched["last_event_at"],
            "tone": matched["manager_status"]["tone"],
        }
    ]
    # Company-level action history: every manager action recorded for this row_id.
    action_history = [
        {
            **record,
            "action_type_label": ACTION_TYPES.get(
                _safe_text(record.get("action_type")),
                _safe_text(record.get("action_label")) or _safe_text(record.get("action_type")),
            ),
        }
        for record in load_manager_actions(job_id)
        if _safe_text(record.get("row_id")) == row_id
    ]
    # Consent records for this company (matched by row_id).
    consents = [
        row
        for row in _load_consents_for_jobs((job_id,))
        if _safe_text(row.get("row_id")) == row_id
    ]
    attempts = [
        item
        for item in _campaign_attempt_rows(job_id)
        if _safe_text(item.get("row_id")) == row_id
    ]
    sent_emails = _company_sent_emails(
        [matched],
        StatsFilters(job_ids=(job_id,)),
    )
    documents = _company_documents([matched])
    company_status_key = _safe_text(
        (matched.get("manager_status") or {}).get("key")
    )
    send_errors = sum(1 for item in attempts if _attempt_has_send_error(item))
    provider_errors = int(
        company_status_key in {"email_broken", "soft_bounce", "delivery_error"}
    )
    return {
        "row_key": matched.get("row_key") or row_key,
        "job_id": job_id,
        "row_id": row_id,
        "organization": matched["organization"],
        "email": matched["email"],
        "emails": matched.get("emails", []),
        "recipient_name": matched["recipient_name"],
        "role_label": matched["role_label"],
        "company": matched.get("company", {}),
        "manager_status": matched["manager_status"],
        "interest": matched["interest"],
        "recommended_action": matched["recommended_action"],
        "next_action": matched["next_action"],
        "status_history": status_history,
        "action_history": action_history,
        "consents": consents,
        "attempts": attempts,
        "sent_emails": sent_emails,
        "documents": documents,
        "summary": {
            "attempts": len(attempts),
            "accepted": sum(1 for item in attempts if _attempt_is_sent(item)),
            "delivered": int(
                company_status_key in {"delivered", "opened", "clicked"}
            ),
            "errors": send_errors + provider_errors,
            "send_errors": send_errors,
            "provider_errors": provider_errors,
            "pending": sum(1 for item in attempts if _attempt_is_pending(item)),
            "sent_emails": len(sent_emails),
            "documents": len(documents),
        },
    }


def _row_key_index(delivery_rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Map ``(job_id, email) -> row_key`` so consent/analytics rows can reuse the
    recipient action flow without a dedicated endpoint."""
    index: dict[tuple[str, str], str] = {}
    for row in delivery_rows:
        row_key = row.get("row_key")
        if not row_key:
            continue
        key = (normalize_job_id(row.get("job_id")), _safe_text(row.get("email")).lower())
        index.setdefault(key, row_key)
    return index


def _row_key_for(index: dict[tuple[str, str], str], job_id: Any, email: Any) -> str | None:
    return index.get((normalize_job_id(job_id), _safe_text(email).lower()))


def build_consents_view(filters: StatsFilters, *, page: int = 1, per_page: int = 10) -> dict[str, Any]:
    rows = _load_company_consents_for_jobs(filters.job_ids)
    if filters.q:
        query = filters.q.lower()
        rows = [
            row
            for row in rows
            if query in _safe_text(row.get("organization")).lower()
            or query in _safe_text(row.get("contact")).lower()
            or query in _safe_text(row.get("email")).lower()
        ]
    if filters.consent_status:
        rows = [row for row in rows if row.get("consent_status_key") == filters.consent_status]
    total = len(rows)
    confirmed = sum(1 for row in rows if row.get("consent_status_key") == "confirmed")
    materials_sent = sum(1 for row in rows if _safe_text(row.get("materials_status")) == "sent" or _safe_text(row.get("materials_sent_at")))
    opened_after = sum(1 for row in rows if row.get("consent_status_key") == "confirmed" and _safe_text(row.get("materials_sent_at")))
    need_call = sum(1 for row in rows if row.get("interest", {}).get("key") == "high")
    start = max(0, (page - 1) * per_page)
    page_rows = rows[start : start + per_page]
    key_index = _row_key_index(_load_delivery_for_jobs(filters.job_ids))
    items = []
    for row in page_rows:
        row_key = _row_key_for(key_index, row.get("job_id"), row.get("email"))
        items.append({**row, "row_key": row_key} if row_key else row)
    consent_base = confirmed or 1
    return {
        "summary": {
            "confirmed": confirmed,
            "materials_sent": materials_sent,
            "opened_after_consent": opened_after,
            "need_call": need_call,
        },
        "funnel": [
            {"id": "consent", "label": "Согласие", "value": confirmed, "percent": _pct(confirmed, consent_base)},
            {"id": "materials", "label": "Материалы отправлены", "value": materials_sent, "percent": _pct(materials_sent, consent_base)},
            {"id": "opened", "label": "Открыли после согласия", "value": opened_after, "percent": _pct(opened_after, consent_base)},
        ],
        "items": items,
        "priority_contacts": sorted(items, key=lambda row: row.get("interest", {}).get("key") != "high")[:5],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": max(1, (total + per_page - 1) // per_page),
        },
    }


def build_email_problems(filters: StatsFilters, *, page: int = 1, per_page: int = 10) -> dict[str, Any]:
    rows = _apply_recipient_filters(_load_companies_for_jobs(filters.job_ids), filters)
    rows = [row for row in rows if row.get("manager_status", {}).get("category") == "problem"]
    total = len(rows)
    hard = sum(1 for row in rows if row.get("manager_status", {}).get("key") == "email_broken")
    soft = sum(1 for row in rows if row.get("manager_status", {}).get("key") == "soft_bounce")
    reasons = Counter(row.get("bounce_reason") for row in rows)
    domains = Counter(row.get("email_domain_provider") for row in rows)
    start = max(0, (page - 1) * per_page)
    page_rows = rows[start : start + per_page]
    return {
        "summary": {
            "problem_addresses": total,
            "hard_bounce": hard,
            "soft_bounce": soft,
            "need_check": hard,
            "retry_later": soft,
        },
        "reasons": [
            {"reason": key, "label": BOUNCE_REASON_LABELS.get(key, key), "count": count, "percent": _pct(count, total)}
            for key, count in reasons.most_common()
        ],
        "domains": [{"provider": key, "count": count} for key, count in domains.most_common()],
        "items": page_rows,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": max(1, (total + per_page - 1) // per_page),
        },
    }


def _campaign_analytics_sections(
    rows: list[dict[str, Any]],
    consent_rows: list[dict[str, Any]],
    *,
    total_attempts: int,
    clicked_override: int | None = None,
) -> dict[str, Any]:
    counts = _aggregate_counts(rows, consent_rows)
    if clicked_override is not None:
        counts["clicked"] = max(0, int(clicked_override))
    counts["total_attempts"] = max(0, int(total_attempts))
    counts["not_sent"] = max(0, counts["total_attempts"] - counts["sent"])

    daily: dict[str, dict[str, int]] = defaultdict(
        lambda: {"sent": 0, "delivered": 0, "opened": 0}
    )
    for row in rows:
        day = _safe_text(row.get("sent_at"))[:10]
        if not day:
            continue
        daily[day]["sent"] += 1
        if row.get("manager_status", {}).get("key") in {"delivered", "opened", "clicked"}:
            daily[day]["delivered"] += 1
        if row.get("manager_status", {}).get("key") in {"opened", "clicked"}:
            daily[day]["opened"] += 1

    reasons = Counter(
        row.get("bounce_reason")
        for row in rows
        if row.get("manager_status", {}).get("category") == "problem"
    )
    provider_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "delivered": 0, "opened": 0}
    )
    for row in rows:
        provider = _provider_label(_safe_text(row.get("provider")) or "unknown")
        provider_stats[provider]["total"] += 1
        if row.get("manager_status", {}).get("key") in {"delivered", "opened", "clicked"}:
            provider_stats[provider]["delivered"] += 1
        if row.get("manager_status", {}).get("key") in {"opened", "clicked"}:
            provider_stats[provider]["opened"] += 1

    high_interest = [
        {
            "organization": row.get("organization"),
            "sent": 1,
            "open_rate": (
                100.0
                if row.get("manager_status", {}).get("key") in {"opened", "clicked"}
                else 0.0
            ),
            "clicked": 1 if row.get("manager_status", {}).get("key") == "clicked" else 0,
        }
        for row in rows
        if row.get("interest", {}).get("key") == "high"
    ][:10]
    key_index = _row_key_index(rows)
    problematic = [
        {
            "email": row.get("email"),
            "organization": row.get("organization"),
            "row_key": _row_key_for(key_index, row.get("job_id"), row.get("email")),
            "reason_label": row.get("bounce_reason_label"),
            "provider_label": _provider_label(row.get("provider")),
            "attempts": row.get("attempts", 1),
        }
        for row in rows
        if row.get("manager_status", {}).get("category") == "problem"
    ][:10]
    insights = build_insights(rows=rows, counts=counts)
    return {
        "summary": counts,
        "rates": {
            "delivery_rate": _pct(counts["delivered"], counts["sent"]),
            "open_rate": _pct(counts["opened"], counts["delivered"] or counts["sent"]),
            "ctr": _pct(counts["clicked"], counts["sent"]),
            "error_rate": _pct(counts["errors"], counts["sent"]),
        },
        "daily": [{"date": day, **values} for day, values in sorted(daily.items())],
        "undelivery_reasons": [
            {
                "reason": key,
                "label": BOUNCE_REASON_LABELS.get(key, key),
                "count": count,
            }
            for key, count in reasons.most_common()
        ],
        "provider_effectiveness": [
            {
                "provider": provider,
                "delivery_rate": _pct(values["delivered"], values["total"]),
                "open_rate": _pct(
                    values["opened"],
                    values["delivered"] or values["total"],
                ),
            }
            for provider, values in provider_stats.items()
        ],
        "funnel": build_funnels(counts=counts),
        "insights": insights,
        "high_interest_companies": high_interest,
        "problem_addresses": problematic,
        "recommendations": [item["text"] for item in insights],
    }


def _step_unique_clickers(step: dict[str, Any]) -> int:
    clickers = {
        _safe_text(clicker.get("recipient_id"))
        or _safe_text(clicker.get("email")).lower()
        or _safe_text(clicker.get("row_id"))
        for link in step.get("links") or []
        for clicker in link.get("clickers") or []
    }
    clickers.discard("")
    return len(clickers)


def _attach_chain_step_analytics(
    job_id: str,
    delivery_rows: list[dict[str, Any]],
    link_analytics: dict[str, Any],
) -> None:
    if _safe_text(link_analytics.get("mode")) != "chain":
        return
    steps = [
        step
        for step in link_analytics.get("steps") or []
        if isinstance(step, dict) and _safe_text(step.get("node_id") or step.get("id"))
    ]
    if not steps:
        return

    from sqlalchemy import func, select

    from src.campaigns.chain_service import get_email_chain
    from src.infra.db import session_scope
    from src.infra.models import (
        Campaign,
        CampaignChainConsentEvent,
        CampaignChainToken,
        DeliveryAttempt,
    )

    step_ids = {
        _safe_text(step.get("node_id") or step.get("id"))
        for step in steps
    }
    root_node_id = _safe_text(steps[0].get("node_id") or steps[0].get("id"))
    tokens: list[CampaignChainToken] = []
    root_attempts = 0
    consent_rows_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with session_scope() as session:
        campaign = session.scalar(
            select(Campaign).where(Campaign.job_id == job_id).limit(1)
        )
        if campaign is not None:
            chain = get_email_chain(campaign, session=session)
            root_node_id = _safe_text(chain.get("root_node_id")) or root_node_id
            root_attempts = int(
                session.scalar(
                    select(func.count())
                    .select_from(DeliveryAttempt)
                    .where(DeliveryAttempt.campaign_id == campaign.id)
                )
                or 0
            )
            tokens = list(
                session.scalars(
                    select(CampaignChainToken)
                    .where(
                        CampaignChainToken.campaign_id == campaign.id,
                        CampaignChainToken.test_email.is_(None),
                    )
                    .order_by(
                        CampaignChainToken.recipient_id.asc(),
                        CampaignChainToken.sent_at.asc(),
                        CampaignChainToken.created_at.asc(),
                    )
                ).all()
            )
            consent_events = session.scalars(
                select(CampaignChainConsentEvent).where(
                    CampaignChainConsentEvent.campaign_id == campaign.id
                )
            ).all()
            seen_consents: set[tuple[str, int]] = set()
            for event in consent_events:
                node_id = _safe_text(event.node_id)
                recipient_id = int(event.recipient_id)
                key = (node_id, recipient_id)
                if node_id not in step_ids or key in seen_consents:
                    continue
                seen_consents.add(key)
                consent_rows_by_node[node_id].append(
                    {
                        "consent_status_key": (
                            "confirmed" if _safe_text(event.action) == "subscribe" else "declined"
                        ),
                    }
                )

    token_candidates: dict[str, list[tuple[int, CampaignChainToken]]] = defaultdict(list)
    for token_index, token in enumerate(tokens):
        target_node_id = _safe_text(token.target_node_id)
        if (
            target_node_id in step_ids
            and token.clicked_at is not None
            and target_node_id != root_node_id
        ):
            token_candidates[_safe_text(token.recipient_id)].append((token_index, token))

    rows_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    used_tokens: set[int] = set()
    sorted_rows = sorted(
        (dict(row) for row in delivery_rows),
        key=lambda row: (
            parsed.timestamp()
            if (
                parsed := _parse_datetime(
                    row.get("sent_at_timestamp") or row.get("sent_at")
                )
            )
            is not None
            else 0.0
        ),
    )
    for row in sorted_rows:
        node_id = _safe_text(row.get("chain_node_id"))
        send_mode = _safe_text(row.get("send_mode")).lower()
        if not node_id and send_mode == "chain_root":
            node_id = root_node_id
        elif not node_id and send_mode == "chain_followup":
            recipient_id = _safe_text(row.get("row_id"))
            available = [
                item
                for item in token_candidates.get(recipient_id, [])
                if item[0] not in used_tokens
            ]
            if available:
                row_time = _parse_datetime(
                    row.get("sent_at_timestamp") or row.get("sent_at")
                )
                if row_time is not None:
                    token_index, token = min(
                        available,
                        key=lambda item: abs(
                            (
                                (item[1].sent_at or item[1].created_at).replace(tzinfo=None)
                                - row_time.replace(tzinfo=None)
                            ).total_seconds()
                        ),
                    )
                else:
                    token_index, token = available[0]
                used_tokens.add(token_index)
                node_id = _safe_text(token.target_node_id)
        if node_id in step_ids:
            row["chain_node_id"] = node_id
            rows_by_node[node_id].append(row)

    total_attempts_by_node: dict[str, int] = defaultdict(int)
    total_attempts_by_node[root_node_id] = root_attempts or len(rows_by_node[root_node_id])
    for token in tokens:
        target_node_id = _safe_text(token.target_node_id)
        if (
            target_node_id in step_ids
            and target_node_id != root_node_id
            and token.clicked_at is not None
        ):
            total_attempts_by_node[target_node_id] += 1

    for step in steps:
        node_id = _safe_text(step.get("node_id") or step.get("id"))
        step_rows = _group_rows_into_companies(rows_by_node.get(node_id, []))
        total_attempts = max(
            total_attempts_by_node.get(node_id, 0),
            len(step_rows),
        )
        step["analytics"] = _campaign_analytics_sections(
            step_rows,
            consent_rows_by_node.get(node_id, []),
            total_attempts=total_attempts,
            clicked_override=_step_unique_clickers(step),
        )


def build_campaign_analytics(
    job_id: str,
    *,
    refresh: bool = False,
    period_from: str = "",
    period_to: str = "",
) -> dict[str, Any]:
    period_from, period_to = normalize_statistics_period(period_from, period_to)
    has_period_filter = bool(period_from or period_to)
    all_delivery_rows = _load_delivery_for_jobs((job_id,))
    all_rows = _group_rows_into_companies(all_delivery_rows)
    refresh_started, refresh_in_progress = _trigger_provider_refresh(
        (job_id,),
        {job_id: all_rows},
        manual=refresh,
        auto=True,
    )
    delivery_rows = _filter_rows_by_period(
        all_delivery_rows,
        period_from=period_from,
        period_to=period_to,
        timestamp_fields=("sent_at_timestamp", "sent_at"),
    )
    rows = _group_rows_into_companies(delivery_rows)
    all_consent_rows = _load_company_consents_for_jobs((job_id,))
    consent_rows = _filter_rows_by_period(
        all_consent_rows,
        period_from=period_from,
        period_to=period_to,
        timestamp_fields=("last_action_at", "materials_sent_at", "created_at"),
    )
    analytics = _campaign_analytics_sections(
        rows,
        consent_rows,
        total_attempts=_campaign_attempt_total(
            job_id,
            period_from=period_from,
            period_to=period_to,
        ),
    )
    counts = analytics["summary"]
    campaign = _campaign_metadata(job_id, rows=all_rows, consent_rows=all_consent_rows)
    campaign_period_from, campaign_period_to = _campaign_period(job_id, all_rows)
    if not has_period_filter:
        period_from = campaign_period_from
        period_to = campaign_period_to
    link_analytics: dict[str, Any] = {
        "mode": "standalone",
        "has_links": False,
        "total_clicks": 0,
        "unique_clickers": 0,
        "steps": [],
    }
    try:
        from src.campaigns.link_analytics_service import build_campaign_link_analytics
        from src.campaigns.service import get_campaign_by_job_id

        campaign_db = get_campaign_by_job_id(job_id)
        if campaign_db:
            link_analytics = build_campaign_link_analytics(job_id, campaign_db)
    except Exception:
        logger.exception("campaign_link_analytics_failed", job_id=job_id)
    try:
        _attach_chain_step_analytics(job_id, delivery_rows, link_analytics)
    except Exception:
        logger.exception("campaign_chain_step_analytics_failed", job_id=job_id)

    if _safe_text(link_analytics.get("mode")) != "chain":
        for step in link_analytics.get("steps") or []:
            if isinstance(step, dict):
                step["analytics"] = analytics
    return {
        "job_id": job_id,
        "campaign": campaign,
        "period_from": period_from,
        "period_to": period_to,
        "status": _campaign_status(job_id),
        "summary": counts,
        "link_analytics": link_analytics,
        "rates": analytics["rates"],
        "daily": analytics["daily"],
        "undelivery_reasons": analytics["undelivery_reasons"],
        "provider_effectiveness": analytics["provider_effectiveness"],
        "funnel": analytics["funnel"],
        "insights": analytics["insights"],
        "high_interest_companies": analytics["high_interest_companies"],
        "problem_addresses": analytics["problem_addresses"],
        "recommendations": analytics["recommendations"],
        "refresh_started": refresh_started,
        "refresh_in_progress": refresh_in_progress,
        "awaiting_provider_events": bool(counts["sent"] > 0 and counts["pending"] >= counts["sent"]),
    }


def _paginate_list(items: list[Any], *, page: int, per_page: int) -> tuple[list[Any], dict[str, int]]:
    page = max(1, page)
    per_page = min(max(1, per_page), 200)
    total = len(items)
    start = (page - 1) * per_page
    return items[start : start + per_page], {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max(1, (total + per_page - 1) // per_page) if total else 1,
    }


def _consents_summary(consent_rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(consent_rows),
        "confirmed": sum(1 for row in consent_rows if row.get("consent_status_key") == "confirmed"),
        "pending": sum(1 for row in consent_rows if row.get("consent_status_key") == "pending"),
        "materials_sent": sum(
            1
            for row in consent_rows
            if row.get("materials_status") == "sent" or _safe_text(row.get("materials_sent_at"))
        ),
    }


def _email_templates_for_campaign(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    from src.campaigns import template_service

    owner = _safe_text(campaign.get("owner_username"))
    templates: list[dict[str, Any]] = []
    for field, template_type in (
        ("email_template_id", "email"),
        ("kp_template_id", "kp"),
        ("contract_template_id", "contract"),
    ):
        template_id = _safe_text(campaign.get(field))
        if not template_id:
            continue
        try:
            tmpl = template_service.get_template(template_id, owner) or {}
        except Exception:
            tmpl = {}
        item: dict[str, Any] = {
            "id": template_id,
            "type": template_type,
            "name": _safe_text(tmpl.get("name")) or template_id,
        }
        if template_type == "email":
            item["preview_image_url"] = f"/api/v1/templates/{template_id}/preview-image"
        templates.append(item)
    return templates


def _documents_summary(job_id: str, *, page: int = 1, per_page: int = 50, query: str = "") -> dict[str, Any]:
    from src.web.download_sources import list_output_archive_entries

    output_dir = resolve_job_paths(job_id).output_dir
    if not output_dir.exists():
        return {"total": 0, "items": [], "pagination": _paginate_list([], page=page, per_page=per_page)[1]}
    offset = max(0, (max(1, page) - 1) * min(max(1, per_page), 200))
    limit = min(max(1, per_page), 200)
    entries, total = list_output_archive_entries(
        output_dir,
        offset=offset,
        limit=limit,
        query=query,
    )
    return {
        "total": total,
        "items": entries,
        "pagination": {
            "page": max(1, page),
            "per_page": limit,
            "total": total,
            "pages": max(1, (total + limit - 1) // limit) if total else 1,
        },
    }


def _campaign_recipients_for_preview(campaign_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from src.infra.db import session_scope
    from src.infra.models import CampaignRecipient

    with session_scope() as session:
        rows = session.scalars(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.excluded.is_(False),
            )
            .order_by(CampaignRecipient.row_index.asc())
            .limit(limit)
        ).all()
        return [
            {
                "id": row.id,
                "row_index": row.row_index,
                "company": row.company,
                "contact_name": row.contact_name,
                "email": row.email,
                "send_status": row.send_status,
                "last_error": row.last_error,
            }
            for row in rows
        ]


def _operational_snapshot(campaign: dict[str, Any] | None, job_id: str) -> dict[str, Any]:
    from src.campaigns.service import list_batches

    if not campaign:
        return {"available": False}
    campaign_id = _safe_text(campaign.get("id"))
    owner = _safe_text(campaign.get("owner_username"))
    batches = list_batches(campaign_id, owner, visible_owners=frozenset({owner})) if owner else []
    live_send: dict[str, Any] | None = None
    if campaign.get("status") in {"running", "scheduled", "paused"}:
        remaining = max(0, int(campaign.get("pending_count") or 0))
        queued = sum(1 for batch in batches if batch.get("status") == "pending")
        running = next((batch for batch in batches if batch.get("status") == "running"), None)
        if remaining > 0 or queued > 0 or running is not None:
            live_send = {
                "status": campaign.get("status"),
                "remaining": remaining,
                "queued_batches": queued,
                "sending_now": running.get("size") if running else 0,
                "next_batch_at": next(
                    (batch.get("scheduled_at") for batch in batches if batch.get("status") == "pending"),
                    None,
                ),
            }
    return {
        "available": True,
        "sent_count": campaign.get("sent_count"),
        "success_count": campaign.get("success_count"),
        "total_count": campaign.get("total_count"),
        "processed_count": campaign.get("processed_count"),
        "pending_count": campaign.get("pending_count"),
        "skipped_count": campaign.get("skipped_count"),
        "failed_recipient_count": campaign.get("failed_recipient_count"),
        "error_count": campaign.get("error_count"),
        "attempt_error_count": campaign.get("attempt_error_count"),
        "layout_error_count": campaign.get("layout_error_count"),
        "status": campaign.get("status"),
        "launched_at": campaign.get("launched_at"),
        "completed_at": campaign.get("completed_at"),
        "transport": campaign.get("transport"),
        "progress": campaign.get("progress"),
        "success_rate": campaign.get("success_rate"),
        "batches": batches,
        "live_send": live_send,
    }


def build_campaign_full_analytics(
    job_id: str,
    *,
    refresh: bool = False,
    period_from: str = "",
    period_to: str = "",
    delivery_page: int = 1,
    sent_log_page: int = 1,
    attempts_page: int = 1,
    documents_page: int = 1,
    documents_q: str = "",
    per_page: int = 50,
) -> dict[str, Any]:
    from src.campaigns.chain_service import get_chain_click_stats
    from src.campaigns.service import get_campaign_by_job_id, list_delivery_attempts
    from src.jobs.job_docs import read_sent_mail_log

    period_from, period_to = normalize_statistics_period(period_from, period_to)
    analytics = build_campaign_analytics(
        job_id,
        refresh=refresh,
        period_from=period_from,
        period_to=period_to,
    )
    campaign_db = get_campaign_by_job_id(job_id)
    campaign_meta = analytics.get("campaign") or {}
    if campaign_db:
        campaign_meta = {
            **campaign_meta,
            **{k: v for k, v in campaign_db.items() if k not in {"draft_payload"}},
        }

    consent_rows = _filter_rows_by_period(
        _load_consents_for_jobs((job_id,)),
        period_from=period_from,
        period_to=period_to,
        timestamp_fields=("last_action_at", "materials_sent_at", "created_at"),
    )
    delivery_rows = _filter_rows_by_period(
        _load_delivery_for_jobs((job_id,), refresh=refresh),
        period_from=period_from,
        period_to=period_to,
        timestamp_fields=("sent_at_timestamp", "sent_at"),
    )
    sent_log = _filter_rows_by_period(
        list(read_sent_mail_log(job_id)),
        period_from=period_from,
        period_to=period_to,
        timestamp_fields=("sent_at", "created_at"),
    )
    sent_page_items, sent_pagination = _paginate_list(
        sent_log,
        page=sent_log_page,
        per_page=per_page,
    )
    delivery_page_items, delivery_pagination = _paginate_list(
        delivery_rows,
        page=delivery_page,
        per_page=per_page,
    )

    domain_filters = StatsFilters(
        job_ids=(normalize_job_id(job_id),),
        period_from=period_from,
        period_to=period_to,
    )
    attempt_created_from, attempt_created_to = _statistics_period_bounds(
        period_from,
        period_to,
    )
    domain_stats = build_domain_delivery_stats(domain_filters)

    chain_stats: dict[str, Any] = {"edges": [], "consents": {}}
    delivery_attempts: dict[str, Any] = {"items": [], "pagination": _paginate_list([], page=1, per_page=per_page)[1]}
    recipients: list[dict[str, Any]] = []
    email_templates: list[dict[str, Any]] = []
    if campaign_db:
        campaign_id = _safe_text(campaign_db.get("id"))
        if campaign_id:
            try:
                chain_stats = get_chain_click_stats(campaign_id)
            except Exception:
                logger.exception("full_analytics_chain_stats_failed", job_id=job_id, campaign_id=campaign_id)
            delivery_attempts = list_delivery_attempts(
                campaign_id,
                page=attempts_page,
                per_page=per_page,
                created_from=attempt_created_from,
                created_to=attempt_created_to,
            )
            recipients = _campaign_recipients_for_preview(campaign_id)
            email_templates = _email_templates_for_campaign(campaign_db)

    counts = analytics.get("summary") or {}
    total_sent = int(counts.get("sent") or 0)
    rates = dict(analytics.get("rates") or {})
    rates["pending_rate"] = _pct(int(counts.get("pending") or 0), total_sent)

    return {
        "job_id": job_id,
        "campaign": campaign_meta,
        "campaign_id": _safe_text(campaign_db.get("id")) if campaign_db else "",
        "period_from": analytics.get("period_from"),
        "period_to": analytics.get("period_to"),
        "status": analytics.get("status"),
        "summary": counts,
        "rates": rates,
        "operational": _operational_snapshot(campaign_db, job_id),
        "delivery": {
            "funnel": analytics.get("funnel"),
            "daily": analytics.get("daily"),
            "undelivery_reasons": analytics.get("undelivery_reasons"),
            "provider_effectiveness": analytics.get("provider_effectiveness"),
            "insights": analytics.get("insights"),
            "high_interest_companies": analytics.get("high_interest_companies"),
            "problem_addresses": analytics.get("problem_addresses"),
            "recommendations": analytics.get("recommendations"),
            "refresh_started": analytics.get("refresh_started"),
            "refresh_in_progress": analytics.get("refresh_in_progress"),
            "awaiting_provider_events": analytics.get("awaiting_provider_events"),
        },
        "domain_stats": domain_stats,
        "chain": chain_stats,
        "consents": _consents_summary(consent_rows),
        "delivery_rows": {
            "items": delivery_page_items,
            "pagination": delivery_pagination,
        },
        "sent_mail_log": {
            "items": sent_page_items,
            "pagination": sent_pagination,
        },
        "delivery_attempts": delivery_attempts,
        "documents": _documents_summary(job_id, page=documents_page, per_page=per_page, query=documents_q),
        "email_templates": email_templates,
        "recipients": recipients,
    }


def list_available_reports() -> list[dict[str, str]]:
    return [
        {
            "id": "delivery_summary",
            "title": "Сводка по доставке",
            "description": "Общая статистика доставки, открытий, кликов, отписок и ошибок.",
        },
        {
            "id": "sent_mail_log",
            "title": "Журнал отправок",
            "description": "Подробный журнал всех отправок по email, получателю и статусу.",
        },
        {
            "id": "consents",
            "title": "Отчёт по согласиям",
            "description": "Аналитика по согласиям, статусам и отправке материалов.",
        },
        {
            "id": "email_problems",
            "title": "Проблемные адреса",
            "description": "Список адресов с ошибками доставки, hard/soft bounce и жалобами.",
        },
        {
            "id": "auto_call_contacts",
            "title": "Контакты для обзвона",
            "description": "CSV со списком телефонов в формате phone_number для автоматического обзвона.",
        },
    ]


def _reports_dir(job_id: str | None) -> Path:
    path = resolve_job_paths(job_id).root_dir / "state" / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_report(
    job_id: str | None,
    *,
    report_type: str,
    fmt: str,
    period_from: str = "",
    period_to: str = "",
    author: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = options or {}
    report_id = uuid.uuid4().hex[:12]
    normalized_fmt = _safe_text(fmt).lower() or "xlsx"
    if report_type == "auto_call_contacts":
        if normalized_fmt != "csv":
            raise ValueError("Контакты для обзвона доступны только в формате CSV.")
        from src.generator.delivery.auto_call_export import build_auto_call_phone_numbers, write_auto_call_csv

        reports_dir = _reports_dir(job_id)
        output_path = reports_dir / f"{report_type}_{report_id}.csv"
        write_auto_call_csv(output_path, build_auto_call_phone_numbers(job_id))
    elif normalized_fmt == "xlsx":
        output_path = build_sender_delivery_report_xlsx(job_id, refresh=bool(options.get("refresh", True)))
    else:
        filters = StatsFilters(job_ids=(normalize_job_id(job_id),) if normalize_job_id(job_id) else ())
        # Delivery-based reports are aggregated per company; the "sent_mail_log"
        # journal stays per email because it is a raw send-by-send log.
        company_rows = _load_companies_for_jobs(filters.job_ids)
        email_rows = _load_delivery_for_jobs(filters.job_ids)
        consent_rows = _load_company_consents_for_jobs(filters.job_ids)
        problems = [row for row in company_rows if row.get("manager_status", {}).get("category") == "problem"]
        actions = load_manager_actions(job_id)
        reports_dir = _reports_dir(job_id)
        rows = email_rows if report_type == "sent_mail_log" else company_rows
        if normalized_fmt == "csv":
            output_path = reports_dir / f"{report_type}_{report_id}.csv"
            _write_csv_report(output_path, report_type, rows, consent_rows, problems, actions)
        elif normalized_fmt == "ndjson":
            output_path = reports_dir / f"{report_type}_{report_id}.ndjson"
            _write_ndjson_report(output_path, report_type, rows, consent_rows, problems, actions)
        else:
            raise ValueError(f"Unsupported export format: {fmt}")

    record = append_report_history(
        job_id,
        report_id=report_id,
        report_type=report_type,
        period_from=period_from,
        period_to=period_to,
        fmt=normalized_fmt,
        author=author,
        status="ready",
        path=str(output_path),
        options=options,
    )
    return {
        "report_id": report_id,
        "path": str(output_path),
        "format": normalized_fmt,
        "record": record,
    }


def _company_field_value(row: dict[str, Any], field: str) -> str:
    fields = (row.get("company") or {}).get("fields") or {}
    return _safe_text(fields.get(field, {}).get("value"))


def _company_emails_text(row: dict[str, Any]) -> str:
    emails = [item.get("email") for item in row.get("emails", []) if item.get("email")]
    return ", ".join(emails) or _safe_text(row.get("email"))


def _write_csv_report(
    path: Path,
    report_type: str,
    rows: list[dict[str, Any]],
    consent_rows: list[dict[str, Any]],
    problems: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> None:
    if report_type == "consents":
        fieldnames = ["organization", "region", "inn", "contact", "email", "consent_status_label", "materials_label", "last_action_at"]
        source = consent_rows
    elif report_type == "email_problems":
        fieldnames = ["organization", "region", "inn", "emails", "bounce_reason_label", "provider", "attempts", "last_event_at"]
        source = problems
    elif report_type == "sent_mail_log":
        fieldnames = ["organization", "email", "provider", "manager_status", "sent_at", "last_event_at"]
        source = rows
    else:
        fieldnames = ["organization", "region", "inn", "emails", "manager_status", "interest", "next_action", "last_event_at"]
        source = rows
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in source:
            if report_type == "consents":
                writer.writerow(
                    {
                        "organization": row.get("organization"),
                        "region": _company_field_value(row, "region"),
                        "inn": _company_field_value(row, "inn"),
                        "contact": row.get("contact"),
                        "email": row.get("email"),
                        "consent_status_label": row.get("consent_status_label"),
                        "materials_label": row.get("materials_label"),
                        "last_action_at": row.get("last_action_at"),
                    }
                )
            elif report_type == "email_problems":
                writer.writerow(
                    {
                        "organization": row.get("organization"),
                        "region": _company_field_value(row, "region"),
                        "inn": _company_field_value(row, "inn"),
                        "emails": _company_emails_text(row),
                        "bounce_reason_label": row.get("bounce_reason_label"),
                        "provider": row.get("provider"),
                        "attempts": row.get("attempts"),
                        "last_event_at": row.get("last_event_at"),
                    }
                )
            elif report_type == "sent_mail_log":
                writer.writerow(
                    {
                        "organization": row.get("organization"),
                        "email": row.get("email"),
                        "provider": row.get("provider"),
                        "manager_status": row.get("manager_status", {}).get("label"),
                        "sent_at": row.get("sent_at"),
                        "last_event_at": row.get("last_event_at"),
                    }
                )
            else:
                writer.writerow(
                    {
                        "organization": row.get("organization"),
                        "region": _company_field_value(row, "region"),
                        "inn": _company_field_value(row, "inn"),
                        "emails": _company_emails_text(row),
                        "manager_status": row.get("manager_status", {}).get("label"),
                        "interest": row.get("interest", {}).get("label"),
                        "next_action": row.get("next_action", {}).get("label"),
                        "last_event_at": row.get("last_event_at"),
                    }
                )
    if report_type == "delivery_summary" and actions:
        with path.open("a", encoding="utf-8-sig", newline="") as handle:
            handle.write("\n")
            writer = csv.DictWriter(
                handle,
                fieldnames=["created_at", "organization", "recipient_email", "action_label", "responsible_manager"],
            )
            writer.writeheader()
            for action in actions:
                writer.writerow(
                    {
                        "created_at": action.get("created_at"),
                        "organization": action.get("organization"),
                        "recipient_email": action.get("recipient_email"),
                        "action_label": action.get("action_label"),
                        "responsible_manager": action.get("responsible_manager"),
                    }
                )


def _write_ndjson_report(
    path: Path,
    report_type: str,
    rows: list[dict[str, Any]],
    consent_rows: list[dict[str, Any]],
    problems: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> None:
    if report_type == "consents":
        source = consent_rows
    elif report_type == "email_problems":
        source = problems
    elif report_type == "sent_mail_log":
        source = rows
    else:
        source = rows
    with path.open("w", encoding="utf-8") as handle:
        for item in source:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        for action in actions:
            handle.write(json.dumps({"manager_action": action}, ensure_ascii=False) + "\n")


def build_reports_view(job_ids: tuple[str, ...]) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    for job_id in job_ids:
        for record in load_report_history(job_id):
            history.append({**record, "job_id": job_id})
    history.sort(key=lambda item: _safe_text(item.get("created_at")), reverse=True)
    formats = Counter(_safe_text(item.get("format")) for item in history if _safe_text(item.get("format")))
    return {
        "available": list_available_reports(),
        "summary": {
            "generated": len(history),
            "xlsx": formats.get("xlsx", 0),
            "ndjson": formats.get("ndjson", 0),
            "csv": formats.get("csv", 0),
        },
        "history": history,
    }


def find_report_file(job_ids: tuple[str, ...], report_id: str) -> Path | None:
    for job_id in job_ids:
        for record in load_report_history(job_id):
            if _safe_text(record.get("report_id")) == report_id:
                path = Path(_safe_text(record.get("path")))
                if path.exists():
                    return path
    return None


def build_domain_delivery_stats(filters: StatsFilters) -> dict[str, Any]:
    rows = _apply_recipient_filters(_load_delivery_for_jobs(filters.job_ids), filters)
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        provider = _safe_text(row.get("email_domain_provider")) or "Другие"
        bucket = buckets.setdefault(
            provider,
            {"provider": provider, "sent": 0, "delivered": 0, "opened": 0, "bounced": 0, "unsubscribed": 0, "spam": 0},
        )
        bucket["sent"] += 1
        status_key = _safe_text(row.get("manager_status", {}).get("key"))
        if status_key in {"delivered", "opened", "clicked"}:
            bucket["delivered"] += 1
        if status_key in {"opened", "clicked"}:
            bucket["opened"] += 1
        if status_key in {"email_broken", "soft_bounce", "delivery_error"}:
            bucket["bounced"] += 1
        if status_key == "unsubscribed":
            bucket["unsubscribed"] += 1
        if status_key == "spam":
            bucket["spam"] += 1
    items = sorted(buckets.values(), key=lambda item: item["sent"], reverse=True)
    return {
        "total_sent": len(rows),
        "providers": items,
    }
