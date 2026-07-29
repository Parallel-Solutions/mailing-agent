"""Parse, validate, and resolve delivery emails for campaign recipients."""

from __future__ import annotations

from typing import Any

from src.generator.delivery.email_validation import (
    EmailValidationResult,
    validate_configured_email_address,
)
from src.generator.delivery.sender_agent import _is_valid_email, _mail_key, _parse_emails, _safe_text
from src.infra.models import CampaignRecipient
from src.utils.config import settings
from src.utils.logger import logger

RECIPIENT_STRATEGY_PRIMARY_THEN_FALLBACK = "primary_then_fallback"


def parse_email_candidates(email: str, email_fallback: str = "") -> list[str]:
    primary = _parse_emails(email)
    extra = _parse_emails(email_fallback)
    ordered = primary + [item for item in extra if item not in primary]
    result: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        candidate = _safe_text(raw).lower()
        key = _mail_key(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def normalize_import_emails(item: dict[str, Any]) -> dict[str, Any]:
    email_raw = str(item.get("email") or "").strip()
    fallback_raw = str(item.get("email_fallback") or "").strip()
    candidates = parse_email_candidates(email_raw, fallback_raw)
    if not candidates:
        return {**item, "email": "", "email_fallback": ""}
    first = candidates[0]
    rest = ", ".join(candidates[1:])
    return {**item, "email": first, "email_fallback": rest}


def validate_email_field(email: str, email_fallback: str = "") -> str:
    candidates = parse_email_candidates(email, email_fallback)
    if not candidates:
        return "empty"
    if any(_is_valid_email(candidate) for candidate in candidates):
        return "valid"
    return "invalid"


def primary_email_key(email: str, email_fallback: str = "") -> str:
    candidates = parse_email_candidates(email, email_fallback)
    return candidates[0] if candidates else ""


def _validate_candidate(
    candidate: str,
    validation_cache: dict[str, EmailValidationResult],
) -> EmailValidationResult:
    cache_key = _mail_key(candidate) or candidate
    cached = validation_cache.get(cache_key)
    if cached is not None:
        return cached
    result = validate_configured_email_address(candidate, config=settings)
    validation_cache[cache_key] = result
    return result


def validate_delivery_email(
    email: str,
    *,
    validation_cache: dict[str, EmailValidationResult] | None = None,
) -> EmailValidationResult:
    """Validate one outgoing recipient with the configured delivery validator."""
    cache = validation_cache if validation_cache is not None else {}
    return _validate_candidate(_safe_text(email), cache)


def _attempt_record(recipient: str, *, error: str, validation: EmailValidationResult | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "recipient": recipient,
        "status": "error",
        "error": error,
    }
    if validation is not None:
        record["validation"] = validation.to_dict()
    return record


def resolve_delivery_email(
    recipient: CampaignRecipient,
    *,
    skip_emails: list[str] | None = None,
    validation_cache: dict[str, EmailValidationResult] | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    from src.generator.delivery.suppression_store import is_suppressed

    cache = validation_cache if validation_cache is not None else {}
    skip_keys = {_mail_key(item) for item in list(skip_emails or []) if _mail_key(item)}
    extra = dict(recipient.extra or {})
    for tried in list(extra.get("tried_emails") or []):
        key = _mail_key(tried)
        if key:
            skip_keys.add(key)

    attempts: list[dict[str, Any]] = []
    for candidate in parse_email_candidates(recipient.email, recipient.email_fallback):
        key = _mail_key(candidate)
        if not key or key in skip_keys:
            continue
        if not _is_valid_email(candidate):
            attempts.append(_attempt_record(candidate, error="Некорректный формат email."))
            continue
        suppressed, suppress_reason = is_suppressed(candidate)
        if suppressed:
            attempts.append(
                _attempt_record(candidate, error=f"Адрес в стоп-листе ({suppress_reason or 'suppressed'}).")
            )
            continue
        result = _validate_candidate(candidate, cache)
        if result.is_valid:
            return candidate, attempts
        reason = _safe_text(result.reason) or "Email не прошёл проверку."
        attempts.append(_attempt_record(candidate, error=reason, validation=result))
    return None, attempts


def remaining_fallback_candidates(recipient: CampaignRecipient, tried_emails: list[str]) -> list[str]:
    tried_keys = {_mail_key(item) for item in tried_emails if _mail_key(item)}
    return [
        candidate
        for candidate in parse_email_candidates(recipient.email, recipient.email_fallback)
        if _mail_key(candidate) not in tried_keys
    ]


def persist_delivery_email_state(
    recipient: CampaignRecipient,
    delivery_email: str,
    *,
    candidates: list[str] | None = None,
) -> None:
    extra = dict(recipient.extra or {})
    normalized = _mail_key(delivery_email) or delivery_email.strip().lower()
    extra["delivery_email"] = normalized
    if candidates is not None:
        extra["email_candidates"] = candidates
    else:
        extra.setdefault("email_candidates", parse_email_candidates(recipient.email, recipient.email_fallback))
    tried = [str(item).strip().lower() for item in list(extra.get("tried_emails") or []) if str(item).strip()]
    tried_keys = {_mail_key(item) for item in tried}
    if normalized and _mail_key(normalized) not in tried_keys:
        tried.append(normalized)
    extra["tried_emails"] = tried
    recipient.extra = extra


def validation_attempts_error(attempts: list[dict[str, Any]]) -> str:
    errors = [_safe_text(item.get("error")) for item in attempts if _safe_text(item.get("error"))]
    return "; ".join(errors) or "Нет email, прошедшего проверку."


def build_campaign_sent_mail_log_record(
    *,
    campaign_id: str,
    recipient_id: int,
    recipient: CampaignRecipient,
    delivery_email: str,
    provider_message_id: str,
    transport: str,
    send_mode: str,
    subject: str,
    campaign_name: str,
    sent_at: str,
    fallback_candidates: list[str] | None = None,
    connection_id: str = "",
) -> dict[str, Any]:
    remaining = fallback_candidates
    if remaining is None:
        tried = list((recipient.extra or {}).get("tried_emails") or [])
        remaining = remaining_fallback_candidates(recipient, tried)
    return {
        "email": delivery_email,
        "recipient": delivery_email,
        "organization": recipient.company,
        "mun_name": recipient.company,
        "row_id": str(recipient_id),
        "status": "sent",
        "transport": transport,
        "connection_id": connection_id,
        "campaign_name": campaign_name,
        "campaign_id": campaign_id,
        "recipient_id": recipient_id,
        "sent_at": sent_at,
        "subject": subject,
        "send_mode": send_mode,
        "provider_message_id": provider_message_id,
        "recipient_strategy": RECIPIENT_STRATEGY_PRIMARY_THEN_FALLBACK,
        "fallback_candidates": remaining,
    }


def append_campaign_sent_mail_log(
    *,
    job_id: str | None,
    campaign_id: str,
    recipient_id: int,
    recipient: CampaignRecipient,
    delivery_email: str,
    provider_message_id: str,
    transport: str,
    send_mode: str,
    subject: str,
    campaign_name: str,
    sent_at: str,
    fallback_candidates: list[str] | None = None,
    connection_id: str = "",
) -> bool:
    if not job_id:
        return False
    try:
        from src.jobs.job_docs import append_event

        record = build_campaign_sent_mail_log_record(
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            recipient=recipient,
            delivery_email=delivery_email,
            provider_message_id=provider_message_id,
            transport=transport,
            send_mode=send_mode,
            subject=subject,
            campaign_name=campaign_name,
            sent_at=sent_at,
            fallback_candidates=fallback_candidates,
            connection_id=connection_id,
        )
        seq = append_event(job_id, "sent_mail_log", record)
        return seq is not None
    except Exception:
        logger.exception(
            "campaign_sent_mail_log_append_failed",
            job_id=job_id,
            campaign_id=campaign_id,
            recipient_id=recipient_id,
        )
        return False
