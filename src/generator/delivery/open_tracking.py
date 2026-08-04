"""First-party open tracking for messages sent through SMTP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import re
import secrets
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.infra.db import session_scope
from src.infra.models import SmtpOpenTracking
from src.utils.config import settings


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
PIXEL_MARKER = 'data-smtp-open-tracking="1"'
OPEN_DEDUP_WINDOW = timedelta(seconds=5)
TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


@dataclass(frozen=True)
class PreparedSmtpOpenTracking:
    token: str
    pixel_url: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_smtp_tracking_delivery_key(
    *,
    connection_id: str,
    recipient: str,
    job_id: str = "",
    campaign_id: str = "",
    row_id: str = "",
    send_mode: str = "",
    send_run_id: str = "",
    warmup_delivery_id: str = "",
    explicit_key: str = "",
) -> str:
    """Build a stable identity; retries of the same logical send reuse its pixel."""
    if explicit_key.strip():
        return "\x1f".join(("explicit", connection_id.strip(), explicit_key.strip()))
    scope = campaign_id.strip() or job_id.strip() or warmup_delivery_id.strip()
    if not scope and not row_id.strip():
        return ""
    return "\x1f".join(
        (
            connection_id.strip(),
            campaign_id.strip(),
            job_id.strip(),
            row_id.strip(),
            send_mode.strip() or "email",
            send_run_id.strip(),
            warmup_delivery_id.strip(),
            recipient.strip().lower(),
        )
    )


def inject_smtp_open_tracking_pixel(html: str, pixel_url: str) -> str:
    if not pixel_url or PIXEL_MARKER in html:
        return html
    pixel = (
        f'<img src="{escape(pixel_url, quote=True)}" width="1" height="1" alt="" '
        'style="display:block;width:1px;height:1px;border:0;opacity:0" '
        f'{PIXEL_MARKER} />'
    )
    if re.search(r"</body\s*>", html, flags=re.IGNORECASE):
        return re.sub(r"</body\s*>", f"{pixel}</body>", html, count=1, flags=re.IGNORECASE)
    return f"{html}{pixel}"


def _prepared_result(token: str) -> PreparedSmtpOpenTracking:
    public_base = str(settings.public_base_url or "").strip().rstrip("/")
    return PreparedSmtpOpenTracking(
        token=token,
        pixel_url=f"{public_base}/public/email/open/{token}.gif",
    )


def prepare_smtp_open_tracking(
    *,
    delivery_key: str,
    connection_id: str,
    owner_username: str,
    recipient: str,
    job_id: str = "",
    campaign_id: str = "",
    row_id: str = "",
    send_mode: str = "",
    warmup_delivery_id: str = "",
) -> PreparedSmtpOpenTracking | None:
    if not settings.smtp_open_tracking_enabled or not delivery_key:
        return None
    if not str(settings.public_base_url or "").strip():
        return None

    delivery_key_hash = sha256(delivery_key.encode("utf-8")).hexdigest()
    try:
        with session_scope() as session:
            existing = session.scalar(
                select(SmtpOpenTracking).where(
                    SmtpOpenTracking.delivery_key_hash == delivery_key_hash
                )
            )
            if existing is not None:
                return _prepared_result(existing.token)
            token = secrets.token_urlsafe(32)
            session.add(
                SmtpOpenTracking(
                    id=str(uuid4()),
                    token=token,
                    delivery_key_hash=delivery_key_hash,
                    connection_id=connection_id or None,
                    owner_username=owner_username,
                    job_id=job_id or None,
                    campaign_id=campaign_id or None,
                    row_id=row_id,
                    warmup_delivery_id=warmup_delivery_id or None,
                    recipient=recipient.strip().lower(),
                    send_mode=send_mode,
                    status="prepared",
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            return _prepared_result(token)
    except IntegrityError:
        # A concurrent retry may have inserted the same delivery identity.
        with session_scope() as session:
            existing = session.scalar(
                select(SmtpOpenTracking).where(
                    SmtpOpenTracking.delivery_key_hash == delivery_key_hash
                )
            )
            if existing is None:
                raise
            return _prepared_result(existing.token)


def mark_smtp_open_tracking_sent(token: str, provider_message_id: str) -> None:
    if not TOKEN_RE.fullmatch(token):
        return
    with session_scope() as session:
        row = session.scalar(
            select(SmtpOpenTracking)
            .where(SmtpOpenTracking.token == token)
            .with_for_update()
        )
        if row is None:
            return
        now = _now()
        row.provider_message_id = provider_message_id or row.provider_message_id
        row.sent_at = row.sent_at or now
        row.status = "opened" if row.first_opened_at is not None else "sent"
        row.updated_at = now


def record_smtp_open(token: str) -> dict[str, object]:
    """Record a pixel load without exposing whether the token exists."""
    if not TOKEN_RE.fullmatch(token):
        return {"found": False, "counted": False, "job_id": ""}

    job_id = ""
    counted = False
    with session_scope() as session:
        row = session.scalar(
            select(SmtpOpenTracking)
            .where(SmtpOpenTracking.token == token)
            .with_for_update()
        )
        if row is None:
            return {"found": False, "counted": False, "job_id": ""}
        now = _now()
        counted = row.last_opened_at is None or now - row.last_opened_at >= OPEN_DEDUP_WINDOW
        if row.first_opened_at is None:
            row.first_opened_at = now
        if counted:
            row.open_count = int(row.open_count or 0) + 1
        row.last_opened_at = now
        row.status = "opened"
        row.updated_at = now
        job_id = str(row.job_id or "")

    if job_id:
        from src.generator.delivery.manager_stats import invalidate_stats_cache

        invalidate_stats_cache(job_id)
    return {"found": True, "counted": counted, "job_id": job_id}


def load_smtp_open_events(job_id: str | None) -> list[dict[str, object]]:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return []
    with session_scope() as session:
        rows = session.scalars(
            select(SmtpOpenTracking)
            .where(
                SmtpOpenTracking.job_id == normalized_job_id,
                SmtpOpenTracking.first_opened_at.is_not(None),
            )
            .order_by(SmtpOpenTracking.last_opened_at.desc())
        ).all()
        return [
            {
                "provider_status": "opened",
                "provider_message_id": row.provider_message_id or "",
                "recipient": row.recipient,
                "row_id": row.row_id,
                "open_count": int(row.open_count or 0),
                "first_opened_at": row.first_opened_at.isoformat() if row.first_opened_at else "",
                "last_opened_at": row.last_opened_at.isoformat() if row.last_opened_at else "",
                "checked_at": row.last_opened_at.isoformat() if row.last_opened_at else "",
            }
            for row in rows
        ]
