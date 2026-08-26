"""Consent and unsubscribe tracking for email chains."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.campaigns.suppression_service import apply_global_email_suppression
from src.generator.delivery.consent_document import write_consent_document
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignChainConsentEvent, CampaignChainToken
from src.infra.object_store import get_bytes, job_key
from src.jobs import resolve_job_paths
from src.jobs.workspace import put_upload
from src.utils.logger import logger

MARKETING_CONSENT_TTL_DAYS = 365
ACTION_SUBSCRIBE = "subscribe"
ACTION_UNSUBSCRIBE = "unsubscribe"
ACTION_MATERIALS_REQUEST = "materials_request"
DOCUMENT_STATUS_PENDING = "pending"
DOCUMENT_STATUS_READY = "ready"
DOCUMENT_STATUS_ERROR = "error"


class MaterialsConsentDocumentError(RuntimeError):
    """The click was recorded, but its evidence document was not persisted."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _existing_event(session: Session, token: str) -> CampaignChainConsentEvent | None:
    return session.execute(
        select(CampaignChainConsentEvent).where(CampaignChainConsentEvent.token == token)
    ).scalar_one_or_none()


def record_subscribe(
    *,
    campaign_id: str,
    recipient_id: int,
    email: str,
    node_id: str,
    edge_id: str,
    token: str,
) -> dict[str, Any]:
    now = _now()
    expires_at = now + timedelta(days=MARKETING_CONSENT_TTL_DAYS)
    with session_scope() as session:
        existing = _existing_event(session, token)
        if existing is not None:
            return {
                "action": ACTION_SUBSCRIBE,
                "created": False,
                "expires_at": existing.expires_at.isoformat() if existing.expires_at else None,
            }
        session.add(
            CampaignChainConsentEvent(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                email=str(email or "").strip().lower(),
                action=ACTION_SUBSCRIBE,
                node_id=node_id,
                edge_id=edge_id,
                token=token,
                created_at=now,
                expires_at=expires_at,
            )
        )
        session.flush()
        return {
            "action": ACTION_SUBSCRIBE,
            "created": True,
            "expires_at": expires_at.isoformat(),
        }


def record_unsubscribe(
    *,
    campaign_id: str,
    recipient_id: int,
    email: str,
    node_id: str,
    edge_id: str,
    token: str,
) -> dict[str, Any]:
    now = _now()
    normalized_email = str(email or "").strip().lower()
    with session_scope() as session:
        existing = _existing_event(session, token)
        if existing is not None:
            return {"action": ACTION_UNSUBSCRIBE, "created": False}
        session.add(
            CampaignChainConsentEvent(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                email=normalized_email,
                action=ACTION_UNSUBSCRIBE,
                node_id=node_id,
                edge_id=edge_id,
                token=token,
                created_at=now,
                expires_at=None,
            )
        )
        session.flush()
    apply_global_email_suppression(
        normalized_email,
        reason="unsubscribe",
        source="chain",
        job_id=campaign_id,
    )
    return {"action": ACTION_UNSUBSCRIBE, "created": True}


def record_materials_request(
    *,
    campaign_id: str,
    recipient_id: int,
    email: str,
    node_id: str,
    edge_id: str,
    token: str,
    ip: str = "",
    user_agent: str = "",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an explicit click requesting the next email and its materials."""

    now = _now()
    with session_scope() as session:
        # Branch tokens exist for every public chain click. Locking the token
        # serializes the initial consent-event insert before its own row exists.
        session.scalar(
            select(CampaignChainToken)
            .where(CampaignChainToken.token == token)
            .with_for_update()
        )
        existing = _existing_event(session, token)
        if existing is not None:
            if existing.action == ACTION_MATERIALS_REQUEST and existing.confirmed_at is None:
                existing.confirmed_at = now
                existing.confirmed_ip = str(ip or "").strip()[:128] or None
                existing.confirmed_user_agent = str(user_agent or "").strip()[:4000] or None
                existing.evidence_payload = dict(evidence or {})
                existing.document_status = DOCUMENT_STATUS_PENDING
                existing.document_error = None
                session.flush()
            return {
                "action": ACTION_MATERIALS_REQUEST,
                "created": False,
                "document_status": existing.document_status,
                "consent_document_path": existing.consent_document_path,
            }
        session.add(
            CampaignChainConsentEvent(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                email=str(email or "").strip().lower(),
                action=ACTION_MATERIALS_REQUEST,
                node_id=node_id,
                edge_id=edge_id,
                token=token,
                created_at=now,
                expires_at=None,
                confirmed_at=now,
                confirmed_ip=str(ip or "").strip()[:128] or None,
                confirmed_user_agent=str(user_agent or "").strip()[:4000] or None,
                evidence_payload=dict(evidence or {}),
                document_status=DOCUMENT_STATUS_PENDING,
            )
        )
        session.flush()
        return {
            "action": ACTION_MATERIALS_REQUEST,
            "created": True,
            "document_status": DOCUMENT_STATUS_PENDING,
            "consent_document_path": None,
        }


def _document_token_part(token: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(token or "").strip()).strip("-_")
    return value[:80] or "unknown"


def ensure_materials_request_document(token: str) -> dict[str, Any]:
    """Create and upload one deterministic DOCX for a materials-request event."""

    caught_error: Exception | None = None
    result: dict[str, Any] | None = None
    job_id = ""
    with session_scope() as session:
        event = session.execute(
            select(CampaignChainConsentEvent)
            .where(CampaignChainConsentEvent.token == token)
            .with_for_update()
        ).scalar_one_or_none()
        if event is None or event.action != ACTION_MATERIALS_REQUEST:
            raise MaterialsConsentDocumentError("Событие согласия на получение материалов не найдено.")
        if event.document_status == DOCUMENT_STATUS_READY and event.consent_document_path:
            return {
                "document_status": DOCUMENT_STATUS_READY,
                "consent_document_path": event.consent_document_path,
                "consent_document_sha256": event.consent_document_sha256,
            }
        campaign = session.get(Campaign, event.campaign_id)
        if campaign is None:
            raise MaterialsConsentDocumentError("Рассылка для события согласия не найдена.")
        evidence = dict(event.evidence_payload or {})
        job_id = str(evidence.get("job_id") or campaign.job_id or campaign.id).strip()
        confirmed_at = event.confirmed_at or event.created_at or _now()
        document_record = {
            **evidence,
            "confirmed_at": confirmed_at.isoformat(),
            "token": event.token,
            "recipient": event.email,
            "mun_name": evidence.get("municipality") or evidence.get("organization") or "",
            "row_id": evidence.get("row_id") or event.recipient_id,
            "confirmed_ip": event.confirmed_ip or "",
            "confirmed_user_agent": event.confirmed_user_agent or "",
        }

        date_part = confirmed_at.date().isoformat()
        relative_path = (
            f"consents/{date_part}/chain-{_document_token_part(token)}_consent.docx"
        )
        local_path = resolve_job_paths(job_id).root_dir / relative_path
        try:
            digest = write_consent_document(local_path, document_record)
            put_upload(job_id, relative_path, local_path)
            stored_digest = hashlib.sha256(
                get_bytes(job_key(job_id, relative_path))
            ).hexdigest()
            if stored_digest != digest:
                raise RuntimeError(
                    "SHA-256 загруженного документа согласия не совпадает с локальным файлом."
                )
            generated_at = _now()
            event.consent_document_path = relative_path
            event.consent_document_sha256 = digest
            event.document_status = DOCUMENT_STATUS_READY
            event.document_error = None
            event.document_generated_at = generated_at
            session.flush()
            result = {
                "document_status": DOCUMENT_STATUS_READY,
                "consent_document_path": relative_path,
                "consent_document_sha256": digest,
            }
        except Exception as exc:
            caught_error = exc
            error_text = str(exc).strip() or exc.__class__.__name__
            event.document_status = DOCUMENT_STATUS_ERROR
            event.document_error = error_text[:4000]
            session.flush()
            logger.exception(
                "chain_materials_consent_document_failed",
                token=token,
                job_id=job_id,
            )

    if caught_error is not None:
        raise MaterialsConsentDocumentError(
            "Не удалось сохранить документ согласия. Повторите переход по ссылке."
        ) from caught_error
    if result is None:
        raise MaterialsConsentDocumentError("Документ согласия не был сохранён.")
    return result


def get_consent_stats(campaign_id: str, *, session: Session | None = None) -> dict[str, Any]:
    def _query(active_session: Session) -> dict[str, Any]:
        rows = active_session.execute(
            select(
                CampaignChainConsentEvent.action,
                func.count(CampaignChainConsentEvent.id).label("total"),
            )
            .where(CampaignChainConsentEvent.campaign_id == campaign_id)
            .group_by(CampaignChainConsentEvent.action)
        ).all()
        counts = {str(r.action): int(r.total) for r in rows}
        return {
            "subscribe": {"count": counts.get(ACTION_SUBSCRIBE, 0)},
            "unsubscribe": {"count": counts.get(ACTION_UNSUBSCRIBE, 0)},
            "materials_request": {
                "count": counts.get(ACTION_MATERIALS_REQUEST, 0)
            },
        }

    if session is not None:
        return _query(session)
    with session_scope() as scoped:
        return _query(scoped)


def has_active_marketing_consent(email: str, *, at: datetime | None = None) -> bool:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return False
    moment = at or _now()
    with session_scope() as session:
        row = session.execute(
            select(CampaignChainConsentEvent)
            .where(
                CampaignChainConsentEvent.email == normalized,
                CampaignChainConsentEvent.action == ACTION_SUBSCRIBE,
                CampaignChainConsentEvent.expires_at.is_not(None),
                CampaignChainConsentEvent.expires_at > moment,
            )
            .order_by(CampaignChainConsentEvent.expires_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row is not None
