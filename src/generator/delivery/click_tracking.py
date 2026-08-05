"""First-party click tracking for messages sent through SMTP.

Mirrors ``open_tracking.py``'s design (stable per-delivery identity via a
hashed delivery key, dedup-safe upserts, never-raise recording) but keyed
per *link* rather than per delivery: one row per (delivery, tracked URL).

Deliberately does not reuse ``CampaignChainToken``/``chain_send_service.py``
content tokens — those remain the working click-tracking mechanism for
email chains. This module closes the actual gap: plain (non-chain)
CampaignFlow SMTP sends, which today get no click tracking at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import secrets
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.infra.db import session_scope
from src.infra.models import SmtpClickTracking
from src.utils.config import settings


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
CLICK_DEDUP_WINDOW = timedelta(seconds=5)

# Same href/text-URL patterns chain_send_service.py uses for its own content
# tokens; kept local to avoid coupling the two independent tracking paths.
_TRACKABLE_HREF_RE = re.compile(
    r"(?P<prefix>\bhref\s*=\s*)(?P<quote>[\"'])(?P<url>https?://[^\"']+)(?P=quote)",
    re.IGNORECASE,
)
_TRACKABLE_TEXT_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


@dataclass(frozen=True)
class PreparedSmtpClickLink:
    token: str
    target_url: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_base() -> str:
    return str(settings.public_base_url or "").strip().rstrip("/")


def _click_url(token: str) -> str:
    return f"{_public_base()}/public/email/click/{token}"


def _prepare_link(
    session,
    *,
    delivery_key_hash: str,
    url: str,
    connection_id: str,
    owner_username: str,
    job_id: str,
    campaign_id: str,
    row_id: str,
    send_mode: str,
    warmup_delivery_id: str,
) -> SmtpClickTracking:
    url_hash = sha256(url.encode("utf-8")).hexdigest()[:16]
    existing = session.scalar(
        select(SmtpClickTracking).where(
            SmtpClickTracking.delivery_key_hash == delivery_key_hash,
            SmtpClickTracking.url_hash == url_hash,
        )
    )
    if existing is not None:
        return existing
    row = SmtpClickTracking(
        id=str(uuid4()),
        token=secrets.token_urlsafe(32),
        delivery_key_hash=delivery_key_hash,
        connection_id=connection_id or None,
        owner_username=owner_username,
        job_id=job_id or None,
        campaign_id=campaign_id or None,
        row_id=row_id,
        warmup_delivery_id=warmup_delivery_id or None,
        recipient="",
        send_mode=send_mode,
        target_url=url,
        link_kind="custom",
        url_hash=url_hash,
        status="prepared",
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    return row


def rewrite_smtp_click_links(
    html: str,
    text: str,
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
) -> tuple[str, str, list[str]]:
    """Rewrite every http(s) link in ``html``/``text`` to a redirect through
    ``/public/email/click/{token}``, creating (or reusing, on retry) one
    ``SmtpClickTracking`` row per unique link for this delivery.

    Returns ``(rewritten_html, rewritten_text, tokens)``. Never raises;
    on any failure the original html/text are returned unchanged.
    """
    if not settings.smtp_click_tracking_enabled or not delivery_key or not _public_base():
        return html, text, []

    from src.campaigns.link_analytics_service import template_links

    declared_links = template_links(html, text)
    if not declared_links:
        return html, text, []

    delivery_key_hash = sha256(delivery_key.encode("utf-8")).hexdigest()
    tracked_urls: dict[str, str] = {}
    tokens: list[str] = []
    try:
        with session_scope() as session:
            for item in declared_links:
                url = str(item.get("url") or "")
                if not url:
                    continue
                row = _prepare_link(
                    session,
                    delivery_key_hash=delivery_key_hash,
                    url=url,
                    connection_id=connection_id,
                    owner_username=owner_username,
                    job_id=job_id,
                    campaign_id=campaign_id,
                    row_id=row_id,
                    send_mode=send_mode,
                    warmup_delivery_id=warmup_delivery_id,
                )
                row.recipient = recipient.strip().lower()
                tracked_urls[url] = _click_url(row.token)
                tokens.append(row.token)
            session.flush()
    except IntegrityError:
        # A concurrent retry of the same logical send raced us; nothing to
        # rewrite this time, the pixel/other path can retry on the next send.
        return html, text, []

    if not tracked_urls:
        return html, text, []

    def replace_href(match: re.Match[str]) -> str:
        from src.campaigns.link_analytics_service import _normalize_url

        normalized = _normalize_url(match.group("url"))
        tracked = tracked_urls.get(normalized)
        if not tracked:
            return match.group(0)
        return f"{match.group('prefix')}{match.group('quote')}{tracked}{match.group('quote')}"

    def replace_text_url(match: re.Match[str]) -> str:
        from src.campaigns.link_analytics_service import _normalize_url

        normalized = _normalize_url(match.group(0))
        return tracked_urls.get(normalized, match.group(0))

    rewritten_html = _TRACKABLE_HREF_RE.sub(replace_href, html or "")
    rewritten_text = _TRACKABLE_TEXT_URL_RE.sub(replace_text_url, text or "")
    return rewritten_html, rewritten_text, tokens


def mark_smtp_click_tracking_sent(tokens: list[str], provider_message_id: str) -> None:
    valid_tokens = [token for token in tokens if TOKEN_RE.fullmatch(token or "")]
    if not valid_tokens:
        return
    with session_scope() as session:
        rows = session.scalars(
            select(SmtpClickTracking).where(SmtpClickTracking.token.in_(valid_tokens))
        ).all()
        now = _now()
        for row in rows:
            row.provider_message_id = provider_message_id or row.provider_message_id
            row.sent_at = row.sent_at or now
            row.status = "clicked" if row.first_clicked_at is not None else "sent"
            row.updated_at = now


def record_smtp_click(token: str) -> dict[str, object]:
    """Record a link click without exposing whether the token exists."""
    if not TOKEN_RE.fullmatch(token or ""):
        return {"found": False, "target_url": ""}

    job_id = ""
    target_url = ""
    link_kind = "custom"
    recipient = ""
    with session_scope() as session:
        row = session.scalar(
            select(SmtpClickTracking).where(SmtpClickTracking.token == token).with_for_update()
        )
        if row is None:
            return {"found": False, "target_url": ""}
        now = _now()
        counted = row.last_clicked_at is None or now - row.last_clicked_at >= CLICK_DEDUP_WINDOW
        if row.first_clicked_at is None:
            row.first_clicked_at = now
        if counted:
            row.click_count = int(row.click_count or 0) + 1
        row.last_clicked_at = now
        row.status = "clicked"
        row.updated_at = now
        job_id = str(row.job_id or "")
        target_url = str(row.target_url or "")
        link_kind = str(row.link_kind or "custom")
        recipient = str(row.recipient or "")

    if job_id:
        try:
            from src.generator.delivery.manager_stats import invalidate_stats_cache

            invalidate_stats_cache(job_id)
        except Exception:
            pass

    if link_kind == "unsubscribe" and recipient:
        try:
            from src.generator.delivery.suppression_store import upsert_from_provider_event

            upsert_from_provider_event(
                recipient=recipient,
                provider_status="unsubscribed",
                source="smtp_click",
                job_id=job_id or None,
            )
        except Exception:
            pass

    return {"found": True, "target_url": target_url}


def load_smtp_click_events(job_id: str | None) -> list[dict[str, object]]:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return []
    with session_scope() as session:
        rows = session.scalars(
            select(SmtpClickTracking)
            .where(
                SmtpClickTracking.job_id == normalized_job_id,
                SmtpClickTracking.first_clicked_at.is_not(None),
            )
            .order_by(SmtpClickTracking.last_clicked_at.desc())
        ).all()
        return [
            {
                "provider_status": "clicked",
                "provider_message_id": row.provider_message_id or "",
                "recipient": row.recipient,
                "row_id": row.row_id,
                "target_url": row.target_url,
                "click_count": int(row.click_count or 0),
                "first_clicked_at": row.first_clicked_at.isoformat() if row.first_clicked_at else "",
                "last_clicked_at": row.last_clicked_at.isoformat() if row.last_clicked_at else "",
            }
            for row in rows
        ]
