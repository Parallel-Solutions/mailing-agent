"""IMAP INBOX scanning for DSN/NDR bounce reports (RFC 3464) and ARF/FBL
spam-complaint reports (RFC 5965), for mailboxes sent through raw SMTP.

Reuses ``imap_sent.py``'s connection/credential machinery (do not duplicate
IMAP auth/TLS logic — divergence there is a security/maintenance risk).
Suppression is always applied for a classified bounce/complaint regardless
of whether it could be attributed to a specific job/campaign: suppression
is a per-email decision, attribution is only for statistics.
"""

from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, message_from_string
from email.message import Message
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.generator.delivery.imap_sent import (
    _imap_utf7_encode,
    _open_imap_connection,
    resolve_imap_credentials,
)
from src.infra.db import session_scope
from src.infra.models import SmtpInboxEvent, SmtpMailbox, SmtpOpenTracking
from src.utils.config import settings
from src.utils.logger import logger


# ARF Feedback-Type values that indicate a spam complaint vs. an unsubscribe
# request routed through the same feedback-loop channel (e.g. some ISPs send
# "opt-out"/"not-spam" reports through the same pipe).
_ARF_COMPLAINT_TYPES = {"abuse", "fraud", "virus", "other"}
_ARF_UNSUBSCRIBE_TYPES = {"opt-out", "unsubscribe"}

_DSN_FAILED_ACTIONS = {"failed"}


@dataclass(frozen=True)
class InboxScanResult:
    status: str  # "scanned" | "disabled" | "not_configured" | "failed"
    messages_seen: int = 0
    bounces_found: int = 0
    complaints_found: int = 0
    error: str = ""


@dataclass(frozen=True)
class DsnReport:
    action: str
    status_code: str
    diagnostic_code: str
    final_recipient: str
    original_recipient: str
    original_message_id: str
    reporting_mta: str


@dataclass(frozen=True)
class ArfReport:
    feedback_type: str
    original_rcpt_to: str
    original_message_id: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _strip_address_type(value: str) -> str:
    """``Final-Recipient: rfc822; user@example.com`` -> ``user@example.com``."""
    text = _safe_text(value)
    if ";" in text:
        _, _, addr = text.partition(";")
        text = addr
    return text.strip().strip("<>").lower()


def _find_part(msg: Message, content_type: str) -> Message | None:
    for part in msg.walk():
        if part is msg:
            continue
        if part.get_content_type() == content_type:
            return part
    return None


def _extract_original_message_id(msg: Message) -> str:
    rfc822_part = _find_part(msg, "message/rfc822")
    if rfc822_part is not None:
        payload = rfc822_part.get_payload()
        if isinstance(payload, list) and payload:
            return _safe_text(payload[0].get("Message-ID"))
    headers_part = _find_part(msg, "text/rfc822-headers")
    if headers_part is not None:
        payload = headers_part.get_payload()
        text = payload if isinstance(payload, str) else ""
        if text:
            try:
                headers_msg = message_from_string(text)
                return _safe_text(headers_msg.get("Message-ID"))
            except Exception:
                return ""
    return ""


def parse_dsn(raw_message: bytes) -> DsnReport | None:
    """Parse an RFC 3464 delivery-status notification.

    Assumes one recipient per DSN (matches our one-message-per-recipient
    sending model) — fields from all delivery-status blocks are merged
    flatly rather than kept per-recipient.
    """
    try:
        msg = message_from_bytes(raw_message)
    except Exception:
        return None
    if not msg.is_multipart() or msg.get_content_type() != "multipart/report":
        return None
    report_type = _safe_text(msg.get_param("report-type", header="Content-Type")).lower()
    if report_type != "delivery-status":
        return None

    status_part = _find_part(msg, "message/delivery-status")
    if status_part is None:
        return None
    blocks = status_part.get_payload()
    if not isinstance(blocks, list) or not blocks:
        return None

    fields: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, Message):
            continue
        for key, value in block.items():
            fields[_safe_text(key).lower()] = _safe_text(value)

    final_recipient = _strip_address_type(fields.get("final-recipient", ""))
    original_recipient = _strip_address_type(fields.get("original-recipient", ""))
    if not final_recipient and not original_recipient:
        return None

    return DsnReport(
        action=fields.get("action", "").lower(),
        status_code=fields.get("status", ""),
        diagnostic_code=fields.get("diagnostic-code", ""),
        # Final-Recipient is what actually bounced (post alias-expansion);
        # prefer it for suppression purposes, per RFC 3464 §2.3.1 trade-off.
        final_recipient=final_recipient or original_recipient,
        original_recipient=original_recipient,
        original_message_id=_extract_original_message_id(msg),
        reporting_mta=fields.get("reporting-mta", ""),
    )


def parse_arf(raw_message: bytes) -> ArfReport | None:
    """Parse an RFC 5965 Abuse Reporting Format (feedback loop) message."""
    try:
        msg = message_from_bytes(raw_message)
    except Exception:
        return None
    if not msg.is_multipart() or msg.get_content_type() != "multipart/report":
        return None
    report_type = _safe_text(msg.get_param("report-type", header="Content-Type")).lower()
    if report_type != "feedback-report":
        return None

    fb_part = _find_part(msg, "message/feedback-report")
    if fb_part is None:
        return None
    payload = fb_part.get_payload()
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], Message):
        return None
    fields = {_safe_text(k).lower(): _safe_text(v) for k, v in payload[0].items()}

    feedback_type = fields.get("feedback-type", "").lower()
    if not feedback_type:
        return None
    original_rcpt = _strip_address_type(fields.get("original-rcpt-to", "") or fields.get("original-mail-from", ""))

    return ArfReport(
        feedback_type=feedback_type,
        original_rcpt_to=original_rcpt,
        original_message_id=_extract_original_message_id(msg),
    )


def _classify_arf_feedback_type(feedback_type: str) -> str | None:
    normalized = _safe_text(feedback_type).lower()
    if normalized in _ARF_COMPLAINT_TYPES:
        return "spam"
    if normalized in _ARF_UNSUBSCRIBE_TYPES:
        return "unsubscribe"
    return None


def _attribute(*, connection_id: str, recipient: str, original_message_id: str) -> tuple[str, str, str]:
    """Best-effort attribution to a job/campaign, for statistics only."""
    if original_message_id:
        with session_scope() as session:
            row = session.scalar(
                select(SmtpOpenTracking).where(
                    SmtpOpenTracking.connection_id == connection_id,
                    SmtpOpenTracking.provider_message_id == original_message_id,
                )
            )
            if row is not None:
                return _safe_text(row.job_id), _safe_text(row.campaign_id), "message_id"

    window_days = int(getattr(settings, "imap_bounce_scan_recipient_window_days", 30) or 30)
    cutoff = _now() - timedelta(days=window_days)
    normalized_recipient = recipient.strip().lower()
    if not normalized_recipient:
        return "", "", "none"
    with session_scope() as session:
        row = session.scalar(
            select(SmtpOpenTracking)
            .where(
                SmtpOpenTracking.connection_id == connection_id,
                SmtpOpenTracking.recipient == normalized_recipient,
                SmtpOpenTracking.sent_at.is_not(None),
                SmtpOpenTracking.sent_at >= cutoff,
            )
            .order_by(SmtpOpenTracking.sent_at.desc())
        )
        if row is not None:
            return _safe_text(row.job_id), _safe_text(row.campaign_id), "recipient_window"
    return "", "", "none"


def _classify_and_apply(
    *,
    connection_id: str,
    raw_message: bytes,
    imap_uid: int,
) -> str | None:
    """Parse, attribute, suppress, and record one bounce/complaint message.
    Returns "bounce"/"complaint"/None (not a DSN/ARF report)."""
    dsn = parse_dsn(raw_message)
    arf = None if dsn is not None else parse_arf(raw_message)
    if dsn is None and arf is None:
        return None

    if dsn is not None:
        event_type = "bounce"
        report_format = "dsn"
        final_recipient = dsn.final_recipient
        action = dsn.action
        status_code = dsn.status_code
        diagnostic_code = dsn.diagnostic_code
        original_message_id = dsn.original_message_id
        reason = None
        if dsn.status_code or dsn.diagnostic_code:
            from src.generator.delivery.suppression_store import reason_from_delivery_response

            reason = reason_from_delivery_response(f"{dsn.status_code} {dsn.diagnostic_code}".strip())
        if reason is None and action not in _DSN_FAILED_ACTIONS:
            # "delayed"/"relayed"/"expanded"/"delivered" DSNs are not failures.
            return None
    else:
        event_type = "complaint"
        report_format = "arf"
        final_recipient = arf.original_rcpt_to
        action = arf.feedback_type
        status_code = ""
        diagnostic_code = ""
        original_message_id = arf.original_message_id
        reason = _classify_arf_feedback_type(arf.feedback_type)

    if not final_recipient:
        return None

    try:
        own_message_id = _safe_text(message_from_bytes(raw_message).get("Message-ID"))
    except Exception:
        own_message_id = ""
    message_id_hash = sha256(
        (own_message_id or f"uid:{connection_id}:{imap_uid}").encode("utf-8")
    ).hexdigest()

    # Idempotency: skip if we've already recorded this exact bounce/complaint
    # message (defense-in-depth alongside the UID cursor, for the crash
    # window between "UID read" and "cursor persisted").
    with session_scope() as session:
        existing = session.scalar(
            select(SmtpInboxEvent.id).where(
                SmtpInboxEvent.connection_id == connection_id,
                SmtpInboxEvent.message_id_hash == message_id_hash,
            )
        )
        if existing is not None:
            return None

    matched_job_id, matched_campaign_id, matched_by = _attribute(
        connection_id=connection_id,
        recipient=final_recipient,
        original_message_id=original_message_id,
    )

    suppression_applied = False
    if reason:
        try:
            from src.generator.delivery.suppression_store import upsert_from_provider_event

            provider_status = {
                "hard_bounce": "hard_bounced",
                "soft_bounce": "soft_bounced",
                "spam": "complaint",
                "unsubscribe": "unsubscribed",
            }.get(reason, reason)
            upsert_from_provider_event(
                recipient=final_recipient,
                provider_status=provider_status,
                source="imap_bounce" if event_type == "bounce" else "imap_complaint",
                job_id=matched_job_id or None,
                delivery_response=diagnostic_code or action,
            )
            suppression_applied = True
        except Exception:
            logger.exception("imap_bounce_suppression_failed", connection_id=connection_id)

    try:
        with session_scope() as session:
            session.add(
                SmtpInboxEvent(
                    connection_id=connection_id,
                    event_type=event_type,
                    report_format=report_format,
                    imap_uid=imap_uid,
                    message_id_hash=message_id_hash,
                    final_recipient=final_recipient,
                    action=action or None,
                    status_code=status_code or None,
                    diagnostic_code=diagnostic_code or None,
                    reason=reason,
                    matched_job_id=matched_job_id or None,
                    matched_campaign_id=matched_campaign_id or None,
                    matched_by=matched_by,
                    suppression_applied=suppression_applied,
                )
            )
    except IntegrityError:
        # A concurrent scan already recorded this exact message.
        pass

    return event_type


def _read_uidvalidity(client: imaplib.IMAP4) -> int:
    try:
        status, data = client.status("INBOX", "(UIDVALIDITY)")
        if status != "OK" or not data or not data[0]:
            return 0
        text = data[0].decode("ascii", errors="ignore") if isinstance(data[0], bytes) else str(data[0])
        match = re.search(r"UIDVALIDITY\s+(\d+)", text)
        return int(match.group(1)) if match else 0
    except Exception:
        return 0


def _fetch_new_uids(client: imaplib.IMAP4, last_uid: int, max_messages: int) -> list[int]:
    status, data = client.uid("SEARCH", None, "UID", f"{last_uid + 1}:*")
    if status != "OK" or not data or not data[0]:
        return []
    raw = data[0].decode("ascii", errors="ignore") if isinstance(data[0], bytes) else str(data[0])
    values = sorted({int(token) for token in raw.split() if token.isdigit()})
    # Some IMAP servers answer "n:*" with the highest existing UID even when
    # nothing is actually >= n; filter defensively.
    values = [uid for uid in values if uid > last_uid]
    return values[:max_messages]


def _fetch_message_raw(client: imaplib.IMAP4, uid: int) -> bytes | None:
    try:
        status, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
    except imaplib.IMAP4.error:
        return None
    if status != "OK" or not data:
        return None
    for part in data:
        if isinstance(part, tuple) and len(part) == 2 and isinstance(part[1], (bytes, bytearray)):
            return bytes(part[1])
    return None


def _persist_cursor(connection_id: str, *, uidvalidity: int, last_uid: int, checked_at: datetime) -> None:
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None:
            return
        row.bounce_scan_uidvalidity = uidvalidity
        row.bounce_scan_last_uid = last_uid
        row.bounce_scan_last_checked_at = checked_at
        row.bounce_scan_last_error = None


def _record_scan_error(connection_id: str, error_text: str) -> None:
    try:
        with session_scope() as session:
            row = session.get(SmtpMailbox, connection_id)
            if row is not None:
                row.bounce_scan_last_error = error_text
                row.bounce_scan_last_checked_at = _now()
    except Exception:
        pass


def scan_inbox_for_bounces(
    *,
    mailbox_id: str,
    owner_username: str,
    max_messages: int = 200,
) -> InboxScanResult:
    """Scan one mailbox's INBOX for new DSN/ARF reports. One IMAP connection,
    one pass, never marks messages as \\Seen (BODY.PEEK)."""
    with session_scope() as session:
        mailbox = session.get(SmtpMailbox, mailbox_id)
        if mailbox is None or mailbox.owner_username != owner_username:
            return InboxScanResult(status="not_configured")
        if not mailbox.bounce_scan_enabled:
            return InboxScanResult(status="disabled")
        last_uid = int(mailbox.bounce_scan_last_uid or 0)
        last_uidvalidity = int(mailbox.bounce_scan_uidvalidity or 0)

    try:
        credentials = resolve_imap_credentials(mailbox_id=mailbox_id, owner_username=owner_username)
    except Exception as exc:
        _record_scan_error(mailbox_id, _safe_text(exc))
        return InboxScanResult(status="not_configured", error=_safe_text(exc))

    if not credentials.host:
        return InboxScanResult(status="not_configured")

    try:
        client = _open_imap_connection(credentials)
    except Exception as exc:
        _record_scan_error(mailbox_id, _safe_text(exc))
        return InboxScanResult(status="failed", error=_safe_text(exc))

    messages_seen = 0
    bounces_found = 0
    complaints_found = 0
    try:
        status, _sel = client.select(_imap_utf7_encode("INBOX"), readonly=True)
        if status != "OK":
            raise RuntimeError("Не удалось открыть папку «Входящие».")

        current_uidvalidity = _read_uidvalidity(client)
        if last_uidvalidity and current_uidvalidity and current_uidvalidity != last_uidvalidity:
            logger.warning(
                "imap_bounce_uidvalidity_reset",
                connection_id=mailbox_id,
                old_uidvalidity=last_uidvalidity,
                new_uidvalidity=current_uidvalidity,
            )
            last_uid = 0

        uids = _fetch_new_uids(client, last_uid, max_messages)
        highest_uid = last_uid
        for uid in uids:
            messages_seen += 1
            highest_uid = max(highest_uid, uid)
            raw = _fetch_message_raw(client, uid)
            if raw is None:
                continue
            try:
                outcome = _classify_and_apply(connection_id=mailbox_id, raw_message=raw, imap_uid=uid)
            except Exception:
                logger.exception("imap_bounce_classify_failed", connection_id=mailbox_id, imap_uid=uid)
                continue
            if outcome == "bounce":
                bounces_found += 1
            elif outcome == "complaint":
                complaints_found += 1

        _persist_cursor(
            mailbox_id,
            uidvalidity=current_uidvalidity or last_uidvalidity,
            last_uid=highest_uid,
            checked_at=_now(),
        )
    except Exception as exc:
        _record_scan_error(mailbox_id, _safe_text(exc))
        return InboxScanResult(status="failed", error=_safe_text(exc))
    finally:
        try:
            client.logout()
        except Exception:
            pass

    return InboxScanResult(
        status="scanned",
        messages_seen=messages_seen,
        bounces_found=bounces_found,
        complaints_found=complaints_found,
    )


def run_inbox_bounce_scan(kwargs: dict[str, Any]) -> None:
    """Entry point wired into background_worker.run_payload for
    task_type="inbox_bounce_scan"."""
    mailbox_id = _safe_text(kwargs.get("mailbox_id"))
    owner_username = _safe_text(kwargs.get("owner_username"))
    if not mailbox_id or not owner_username:
        return
    max_messages = int(getattr(settings, "imap_bounce_scan_max_messages_per_run", 200) or 200)
    result = scan_inbox_for_bounces(
        mailbox_id=mailbox_id,
        owner_username=owner_username,
        max_messages=max_messages,
    )
    logger.info(
        "imap_bounce_scan_completed",
        connection_id=mailbox_id,
        status=result.status,
        messages_seen=result.messages_seen,
        bounces_found=result.bounces_found,
        complaints_found=result.complaints_found,
        error=result.error,
    )
