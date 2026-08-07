"""Background SMTP.BZ validation and persistent recipient validation cache."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.generator.delivery.email_validation import (
    EMAIL_VALIDATION_SMTPBZ,
    EmailValidationResult,
    normalize_email_validation_mode,
    validate_configured_email_address,
    validate_email_address,
)
from src.infra.db import session_scope
from src.infra.models import (
    Audience,
    AudienceMember,
    Campaign,
    CampaignRecipient,
    EmailValidationCache,
    EmailValidationRun,
)
from src.utils.config import settings
from src.utils.logger import logger


PROVIDER = "smtpbz"
ACTIVE_RUN_STATUSES = {"queued", "running"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "stale"}
VALIDATION_STATUSES = {"pending", "valid", "invalid", "unknown", "stale"}
TRANSIENT_REASON_CODES = {
    "smtpbz_unavailable",
}
HARD_FAILURE_STATUSES = {
    "hard_bounced",
    "hard_bounce",
    "email_broken",
    "err_user_unknown",
    "err_user_inactive",
    "err_recipient_inactive",
    "user_unknown",
    "not_found",
    "invalid_recipient",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def smtpbz_preflight_enabled() -> bool:
    return normalize_email_validation_mode(settings.email_validation_mode) == EMAIL_VALIDATION_SMTPBZ


def _scope_entity(session: Any, scope_type: str, scope_id: str, owner_username: str) -> Any:
    if scope_type == "campaign":
        row = session.get(Campaign, scope_id)
    elif scope_type == "audience":
        row = session.get(Audience, scope_id)
    else:
        raise ValueError("Unsupported email validation scope.")
    if row is None or str(row.owner_username) != str(owner_username):
        raise LookupError("Email validation scope not found.")
    return row


def _scope_rows(session: Any, scope_type: str, scope_id: str) -> list[Any]:
    if scope_type == "campaign":
        return list(
            session.scalars(
                select(CampaignRecipient)
                .where(CampaignRecipient.campaign_id == scope_id)
                .order_by(CampaignRecipient.row_index, CampaignRecipient.id)
            ).all()
        )
    if scope_type == "audience":
        return list(
            session.scalars(
                select(AudienceMember)
                .where(AudienceMember.audience_id == scope_id)
                .order_by(AudienceMember.id)
            ).all()
        )
    raise ValueError("Unsupported email validation scope.")


def _candidate_emails(row: Any) -> list[str]:
    from src.campaigns.recipient_email_service import parse_email_candidates

    return parse_email_candidates(str(row.email or ""), str(row.email_fallback or ""))


def _scope_candidates(rows: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for candidate in _candidate_emails(row):
            normalized = candidate.strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
    return result


def _scope_revision(candidates: list[str]) -> str:
    raw = "\n".join(sorted(candidates)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cache_is_fresh(row: EmailValidationCache, now: datetime | None = None) -> bool:
    current = now or _now()
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > current and str(row.status or "") in {"valid", "invalid", "unknown"}


def _cache_expiry(status: str, now: datetime) -> datetime:
    if status == "valid":
        return now + timedelta(days=max(1, int(settings.email_validation_valid_ttl_days or 14)))
    if status == "invalid":
        return now + timedelta(days=max(1, int(settings.email_validation_invalid_ttl_days or 30)))
    return now + timedelta(minutes=max(1, int(settings.email_validation_unknown_ttl_minutes or 15)))


def _run_is_stuck(row: EmailValidationRun, now: datetime | None = None) -> bool:
    if str(row.status or "") != "running":
        return False
    current = now or _now()
    updated_at = row.updated_at or row.started_at or row.created_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    try:
        request_timeout = max(1.0, float(settings.email_validation_timeout_seconds or 10.0))
    except (TypeError, ValueError):
        request_timeout = 10.0
    attempts = max(1, int(settings.email_validation_max_attempts or 2))
    stale_after = max(120.0, request_timeout * attempts + 30.0)
    return (current - updated_at).total_seconds() >= stale_after


def _result_status(result: EmailValidationResult) -> str:
    if result.reason_code == "ok_smtpbz" and result.is_valid:
        return "valid"
    if result.reason_code in {"smtpbz_invalid", "delivery_hard_bounce"}:
        return "invalid"
    return "unknown"


def _cache_payload(row: EmailValidationCache) -> dict[str, Any]:
    return {
        "email": row.normalized_email,
        "status": row.status,
        "reason_code": row.reason_code,
        "reason": row.reason or "",
        "checked_at": row.checked_at.isoformat() if row.checked_at else "",
        "expires_at": row.expires_at.isoformat() if row.expires_at else "",
        "details": dict(row.details or {}),
    }


def cached_validation_result(owner_username: str, email: str) -> EmailValidationResult:
    syntax = validate_email_address(email, mode="syntax")
    if not syntax.is_valid or not smtpbz_preflight_enabled():
        return syntax

    with session_scope() as session:
        row = session.scalar(
            select(EmailValidationCache).where(
                EmailValidationCache.owner_username == owner_username,
                EmailValidationCache.provider == PROVIDER,
                EmailValidationCache.normalized_email == syntax.normalized_email.lower(),
            )
        )
        if row is None or not _cache_is_fresh(row):
            return EmailValidationResult(
                email=email,
                normalized_email=syntax.normalized_email,
                domain=syntax.domain,
                is_valid=False,
                reason_code="validation_pending",
                reason="Email ещё не прошёл предварительную проверку SMTP.BZ.",
                checked_at=_now().isoformat(timespec="seconds"),
                details={"mode": PROVIDER, "status": "pending"},
            )
        return EmailValidationResult(
            email=email,
            normalized_email=syntax.normalized_email,
            domain=syntax.domain,
            is_valid=row.status == "valid",
            reason_code=row.reason_code or f"smtpbz_{row.status}",
            reason=row.reason or "",
            checked_at=row.checked_at.isoformat(timespec="seconds"),
            details=dict(row.details or {}),
        )


def _upsert_cache(
    owner_username: str,
    email: str,
    result: EmailValidationResult,
    *,
    attempt_count: int,
) -> tuple[str, dict[str, Any]]:
    now = _now()
    status = _result_status(result)
    values = {
        "id": str(uuid4()),
        "owner_username": owner_username,
        "provider": PROVIDER,
        "normalized_email": email,
        "status": status,
        "reason_code": result.reason_code,
        "reason": result.reason or None,
        "details": dict(result.details or {}),
        "attempt_count": max(1, int(attempt_count)),
        "checked_at": now,
        "expires_at": _cache_expiry(status, now),
        "updated_at": now,
    }
    with session_scope() as session:
        statement = pg_insert(EmailValidationCache).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["owner_username", "provider", "normalized_email"],
            set_={key: value for key, value in values.items() if key not in {"id", "owner_username", "provider", "normalized_email"}},
        )
        session.execute(statement)
        row = session.scalar(
            select(EmailValidationCache).where(
                EmailValidationCache.owner_username == owner_username,
                EmailValidationCache.provider == PROVIDER,
                EmailValidationCache.normalized_email == email,
            )
        )
        assert row is not None
        return status, _cache_payload(row)


def _validate_one(
    owner_username: str,
    email: str,
    *,
    refresh_unknown: bool = False,
    skip_cache_lookup: bool = False,
) -> tuple[str, dict[str, Any], bool]:
    now = _now()
    if not skip_cache_lookup:
        with session_scope() as session:
            cached = session.scalar(
                select(EmailValidationCache).where(
                    EmailValidationCache.owner_username == owner_username,
                    EmailValidationCache.provider == PROVIDER,
                    EmailValidationCache.normalized_email == email,
                )
            )
            if (
                cached is not None
                and _cache_is_fresh(cached, now)
                and not (refresh_unknown and cached.status == "unknown")
            ):
                return str(cached.status), _cache_payload(cached), True

    max_attempts = max(1, int(settings.email_validation_max_attempts or 2))
    last_result: EmailValidationResult | None = None
    for attempt in range(1, max_attempts + 1):
        last_result = validate_configured_email_address(email, config=settings)
        status = _result_status(last_result)
        if status != "unknown" or last_result.reason_code not in TRANSIENT_REASON_CODES:
            saved_status, payload = _upsert_cache(
                owner_username,
                email,
                last_result,
                attempt_count=attempt,
            )
            return saved_status, payload, False
        if attempt < max_attempts:
            time.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))

    assert last_result is not None
    saved_status, payload = _upsert_cache(
        owner_username,
        email,
        last_result,
        attempt_count=max_attempts,
    )
    return saved_status, payload, False


def _fresh_cached_results(
    owner_username: str,
    candidates: list[str],
    *,
    refresh_unknown: bool,
) -> dict[str, tuple[str, dict[str, Any], bool]]:
    """Load the complete validation cache in one DB round-trip."""
    if not candidates:
        return {}
    now = _now()
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(EmailValidationCache).where(
                    EmailValidationCache.owner_username == owner_username,
                    EmailValidationCache.provider == PROVIDER,
                    EmailValidationCache.normalized_email.in_(candidates),
                )
            ).all()
        )
    result: dict[str, tuple[str, dict[str, Any], bool]] = {}
    for row in rows:
        if not _cache_is_fresh(row, now):
            continue
        if refresh_unknown and str(row.status or "") == "unknown":
            continue
        result[str(row.normalized_email)] = (
            str(row.status),
            _cache_payload(row),
            True,
        )
    return result


def _update_run_progress(
    run_id: str,
    results: list[tuple[str, dict[str, Any], bool]],
) -> None:
    """Persist progress in batches instead of one transaction per address."""
    if not results:
        return
    valid_count = sum(1 for status, _item, _cached in results if status == "valid")
    invalid_count = sum(1 for status, _item, _cached in results if status == "invalid")
    unknown_count = len(results) - valid_count - invalid_count
    cached_count = sum(1 for _status, _item, cached in results if cached)
    with session_scope() as session:
        run = session.get(EmailValidationRun, run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        run.processed_count = int(run.processed_count or 0) + len(results)
        run.valid_count = int(run.valid_count or 0) + valid_count
        run.invalid_count = int(run.invalid_count or 0) + invalid_count
        run.unknown_count = int(run.unknown_count or 0) + unknown_count
        run.cached_count = int(run.cached_count or 0) + cached_count
        run.updated_at = _now()


def _apply_scope_results(session: Any, run: EmailValidationRun) -> dict[str, int]:
    rows = _scope_rows(session, run.scope_type, run.scope_id)
    candidates = _scope_candidates(rows)
    cache_rows = list(
        session.scalars(
            select(EmailValidationCache).where(
                EmailValidationCache.owner_username == run.owner_username,
                EmailValidationCache.provider == PROVIDER,
                EmailValidationCache.normalized_email.in_(candidates or [""]),
            )
        ).all()
    )
    now = _now()
    cache = {
        row.normalized_email: row
        for row in cache_rows
        if _cache_is_fresh(row, now)
    }
    counts = {status: 0 for status in ("pending", "valid", "invalid", "unknown", "stale")}
    for recipient in rows:
        emails = _candidate_emails(recipient)
        results = [cache.get(email.lower()) for email in emails]
        if not emails:
            status = "invalid"
        elif any(item is not None and item.status == "valid" for item in results):
            status = "valid"
        elif results and all(item is not None and item.status == "invalid" for item in results):
            status = "invalid"
        elif any(item is not None and item.status == "unknown" for item in results):
            status = "unknown"
        else:
            status = "pending"

        extra = dict(recipient.extra or {})
        was_validation_excluded = bool(extra.get("validation_excluded"))
        extra["email_validation"] = {
            "provider": PROVIDER,
            "status": status,
            "candidates": [
                _cache_payload(item) if item is not None else {"email": email, "status": "pending"}
                for email, item in zip(emails, results)
            ],
            "revision": run.revision,
            "updated_at": now.isoformat(),
        }
        if status in {"invalid", "unknown"}:
            recipient.excluded = True
            extra["validation_excluded"] = True
        elif was_validation_excluded and status == "valid":
            recipient.excluded = False
            extra["validation_excluded"] = False
        recipient.validation_status = status
        recipient.extra = extra
        counts[status] += 1

    if run.scope_type == "audience":
        audience = session.get(Audience, run.scope_id)
        if audience is not None:
            audience.quality_score = round(100.0 * counts["valid"] / len(rows), 1) if rows else 0.0
            audience.updated_at = now
    return counts


def _run_payload(row: EmailValidationRun) -> dict[str, Any]:
    total = max(0, int(row.total_count or 0))
    processed = max(0, int(row.processed_count or 0))
    return {
        "id": str(row.id),
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "revision": row.revision,
        "provider": row.provider,
        "status": row.status,
        "total_count": total,
        "processed_count": processed,
        "valid_count": int(row.valid_count or 0),
        "invalid_count": int(row.invalid_count or 0),
        "unknown_count": int(row.unknown_count or 0),
        "cached_count": int(row.cached_count or 0),
        "progress_percent": round(100.0 * processed / total, 1) if total else 100.0,
        "task_id": str(row.task_id or ""),
        "error": row.error or "",
        "started_at": row.started_at.isoformat() if row.started_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def get_scope_validation(scope_type: str, scope_id: str, owner_username: str) -> dict[str, Any]:
    with session_scope() as session:
        _scope_entity(session, scope_type, scope_id, owner_username)
        latest = session.scalar(
            select(EmailValidationRun)
            .where(
                EmailValidationRun.owner_username == owner_username,
                EmailValidationRun.scope_type == scope_type,
                EmailValidationRun.scope_id == scope_id,
            )
            .order_by(EmailValidationRun.created_at.desc())
            .limit(1)
        )
        rows = _scope_rows(session, scope_type, scope_id)
        counts = {status: 0 for status in VALIDATION_STATUSES}
        for row in rows:
            status = str(row.validation_status or "pending")
            counts[status if status in counts else "pending"] += 1
        payload = _run_payload(latest) if latest is not None else {
            "id": "",
            "scope_type": scope_type,
            "scope_id": scope_id,
            "revision": _scope_revision(_scope_candidates(rows)),
            "provider": PROVIDER,
            "status": "not_started",
            "total_count": len(_scope_candidates(rows)),
            "processed_count": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "unknown_count": 0,
            "cached_count": 0,
            "progress_percent": 0.0,
            "task_id": "",
            "error": "",
            "started_at": "",
            "completed_at": "",
            "created_at": "",
        }
        payload["recipient_counts"] = counts
        payload["enabled"] = smtpbz_preflight_enabled()
        return payload


def enqueue_scope_validation(
    scope_type: str,
    scope_id: str,
    owner_username: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    if not smtpbz_preflight_enabled():
        return get_scope_validation(scope_type, scope_id, owner_username)

    superseded_task_id: str | None = None
    replacement_run = False
    with session_scope() as session:
        _scope_entity(session, scope_type, scope_id, owner_username)
        rows = _scope_rows(session, scope_type, scope_id)
        candidates = _scope_candidates(rows)
        revision = _scope_revision(candidates)
        existing = session.scalar(
            select(EmailValidationRun)
            .where(
                EmailValidationRun.owner_username == owner_username,
                EmailValidationRun.scope_type == scope_type,
                EmailValidationRun.scope_id == scope_id,
                EmailValidationRun.revision == revision,
                EmailValidationRun.status.in_(ACTIVE_RUN_STATUSES | ({"completed"} if not force else set())),
            )
            .order_by(EmailValidationRun.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            if not (force and _run_is_stuck(existing)):
                return _run_payload(existing)
            existing.status = "stale"
            existing.error = "Validation stopped updating and was superseded by a retry."
            existing.completed_at = _now()
            existing.updated_at = _now()
            superseded_task_id = str(existing.task_id or "").strip() or None
            replacement_run = True

        for row in rows:
            current_status = str(row.validation_status or "pending")
            if force:
                if current_status in {"pending", "unknown", "stale"}:
                    row.validation_status = "pending"
            elif current_status != "invalid":
                row.validation_status = "pending"
        run = EmailValidationRun(
            id=str(uuid4()),
            owner_username=owner_username,
            scope_type=scope_type,
            scope_id=scope_id,
            revision=revision,
            provider=PROVIDER,
            status="queued",
            total_count=len(candidates),
        )
        session.add(run)
        session.flush()
        run_id = str(run.id)

    from src.workers.task_queue import enqueue_task, request_cancel

    if superseded_task_id:
        try:
            request_cancel(superseded_task_id)
        except Exception:
            logger.exception(
                "email_validation_cancel_stuck_task_failed",
                run_id=run_id,
                task_id=superseded_task_id,
            )

    try:
        task, _created = enqueue_task(
            task_type="email_validation",
            job_id=None,
            owner_username=owner_username,
            payload={"run_id": run_id, "refresh_unknown": bool(force)},
            max_attempts=max(1, int(settings.background_queue_max_attempts or 3)),
            idempotency_key=f"email_validation:{run_id}",
            active_key=(
                f"email_validation:{owner_username}:{scope_type}:{scope_id}:{revision}:{run_id}"
                if replacement_run
                else f"email_validation:{owner_username}:{scope_type}:{scope_id}:{revision}"
            ),
        )
    except Exception as exc:
        mark_validation_run_failed(run_id, str(exc))
        raise

    with session_scope() as session:
        run = session.get(EmailValidationRun, run_id)
        if run is not None:
            run.task_id = str(task.get("id") or "") or None
            run.updated_at = _now()
            return _run_payload(run)
    raise RuntimeError("Email validation run disappeared after enqueue.")


def run_email_validation(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")

    with session_scope() as session:
        run = session.get(EmailValidationRun, run_id)
        if run is None:
            raise LookupError("Email validation run not found.")
        if run.status in TERMINAL_RUN_STATUSES:
            return _run_payload(run)
        run.status = "running"
        run.started_at = run.started_at or _now()
        run.error = None
        rows = _scope_rows(session, run.scope_type, run.scope_id)
        candidates = _scope_candidates(rows)
        current_revision = _scope_revision(candidates)
        if current_revision != run.revision:
            run.status = "stale"
            run.completed_at = _now()
            return _run_payload(run)
        run.processed_count = 0
        run.valid_count = 0
        run.invalid_count = 0
        run.unknown_count = 0
        run.cached_count = 0
        run.total_count = len(candidates)
        owner_username = run.owner_username

    concurrency = max(1, min(20, int(settings.email_validation_concurrency or 10)))
    refresh_unknown = bool(payload.get("refresh_unknown"))
    cached_results = _fresh_cached_results(
        owner_username,
        candidates,
        refresh_unknown=refresh_unknown,
    )
    _update_run_progress(run_id, list(cached_results.values()))
    network_candidates = [email for email in candidates if email not in cached_results]
    progress_batch: list[tuple[str, dict[str, Any], bool]] = []
    progress_batch_size = max(5, concurrency)
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="email-validation") as pool:
        futures = {
            pool.submit(
                _validate_one,
                owner_username,
                email,
                refresh_unknown=refresh_unknown,
                skip_cache_lookup=True,
            ): email
            for email in network_candidates
        }
        for future in as_completed(futures):
            email = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.exception("email_validation_item_failed", run_id=run_id, email=email)
                fallback = EmailValidationResult(
                    email=email,
                    normalized_email=email,
                    domain=email.rsplit("@", 1)[-1] if "@" in email else "",
                    is_valid=False,
                    reason_code="smtpbz_unavailable",
                    reason=f"SMTP.BZ validation failed: {type(exc).__name__}",
                    checked_at=_now().isoformat(timespec="seconds"),
                    details={"status": "error"},
                )
                status, item = _upsert_cache(owner_username, email, fallback, attempt_count=1)
                result = (status, item, False)
            progress_batch.append(result)
            if len(progress_batch) >= progress_batch_size:
                _update_run_progress(run_id, progress_batch)
                progress_batch = []
    _update_run_progress(run_id, progress_batch)

    with session_scope() as session:
        run = session.get(EmailValidationRun, run_id)
        if run is None:
            raise LookupError("Email validation run not found.")
        if run.status in TERMINAL_RUN_STATUSES:
            return _run_payload(run)
        rows = _scope_rows(session, run.scope_type, run.scope_id)
        if _scope_revision(_scope_candidates(rows)) != run.revision:
            run.status = "stale"
        else:
            _apply_scope_results(session, run)
            run.status = "completed"
        run.completed_at = _now()
        run.updated_at = _now()
        return _run_payload(run)


def mark_validation_run_failed(run_id: str, error: str) -> None:
    with session_scope() as session:
        run = session.get(EmailValidationRun, str(run_id))
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        run.status = "failed"
        run.error = str(error or "Email validation failed.")[:2000]
        run.completed_at = _now()
        run.updated_at = _now()


def record_hard_delivery_failure(
    *,
    owner_username: str,
    email: str,
    provider_status: str,
    reason: str = "",
) -> bool:
    normalized_status = str(provider_status or "").strip().lower()
    from src.generator.delivery.suppression_store import (
        reason_from_delivery_response,
        reason_from_provider_status,
    )

    suppression_reason = reason_from_provider_status(normalized_status)
    suppression_reason = suppression_reason or reason_from_delivery_response(reason)
    if normalized_status not in HARD_FAILURE_STATUSES and suppression_reason != "hard_bounce":
        return False
    syntax = validate_email_address(email, mode="syntax")
    if not syntax.is_valid:
        return False
    now = _now()
    fallback = EmailValidationResult(
        email=email,
        normalized_email=syntax.normalized_email,
        domain=syntax.domain,
        is_valid=False,
        reason_code="delivery_hard_bounce",
        reason=reason or f"Провайдер сообщил окончательную недоставку: {normalized_status}.",
        checked_at=now.isoformat(timespec="seconds"),
        details={"source": "delivery_webhook", "provider_status": normalized_status},
    )
    _upsert_cache(owner_username, syntax.normalized_email.lower(), fallback, attempt_count=1)
    return True
