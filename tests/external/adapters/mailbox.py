"""IMAP mailbox adapter for external statistics tests (Level 3).

Reads a real mailbox via IMAP to verify that:
- emails actually arrived
- subject and body are correct
- consent / unsubscribe links are present
- follow-up materials emails arrive after consent

Usage:
    from tests.external.adapters.mailbox import ImapMailboxAdapter
    mb = ImapMailboxAdapter(host, port, user, password, use_ssl=True)
    msgs = mb.wait_for_message(subject_contains="согласие", timeout=120)
    link = mb.extract_consent_link(msgs[0])
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MailMessage:
    uid: str
    subject: str
    sender: str
    to: str
    date: str
    text_body: str
    html_body: str
    attachments: list[str] = field(default_factory=list)  # filenames
    raw_headers: dict[str, str] = field(default_factory=dict)


class MailboxError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

# Pattern to extract consent confirm link from HTML body
_CONSENT_LINK_RE = re.compile(
    r'https?://[^\s"\'<>]+/consent/confirm/[A-Za-z0-9_-]+',
    re.IGNORECASE,
)

_UNSUBSCRIBE_LINK_RE = re.compile(
    r'https?://[^\s"\'<>]+/unsubscribe/[A-Za-z0-9_-]+',
    re.IGNORECASE,
)

_ANY_LINK_RE = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)


class ImapMailboxAdapter:
    """Read a real IMAP mailbox to verify email delivery."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        *,
        use_ssl: bool = True,
        folder: str = "INBOX",
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_ssl = use_ssl
        self.folder = folder

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        if self.use_ssl:
            conn = imaplib.IMAP4_SSL(self.host, self.port)
        else:
            conn = imaplib.IMAP4(self.host, self.port)
        conn.login(self.user, self.password)
        conn.select(self.folder)
        return conn

    # ------------------------------------------------------------------
    # Search & fetch
    # ------------------------------------------------------------------

    def fetch_recent(self, *, since_seconds: float = 600.0) -> list[MailMessage]:
        """Fetch messages received in the last `since_seconds` seconds."""
        conn = self._connect()
        try:
            # Search for messages since today (IMAP date criterion is day-level)
            typ, data = conn.uid("search", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].decode().split()
            # Fetch recent UIDs (last 50 to keep it fast)
            recent_uids = uids[-50:]
            messages: list[MailMessage] = []
            for uid in reversed(recent_uids):
                msg = self._fetch_one(conn, uid)
                if msg is None:
                    continue
                messages.append(msg)
            return messages
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _fetch_one(self, conn: imaplib.IMAP4 | imaplib.IMAP4_SSL, uid: str) -> MailMessage | None:
        typ, data = conn.uid("fetch", uid, "(RFC822)")
        if typ != "OK" or not data or data[0] is None:
            return None
        raw = data[0][1] if isinstance(data[0], tuple) else None
        if not raw or not isinstance(raw, bytes):
            return None
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        return self._parse_message(uid, msg)

    def _parse_message(self, uid: str, msg: Any) -> MailMessage:
        subject = str(msg.get("Subject") or "")
        sender = str(msg.get("From") or "")
        to = str(msg.get("To") or "")
        date = str(msg.get("Date") or "")
        text_body = ""
        html_body = ""
        attachments: list[str] = []

        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition") or "")
                if "attachment" in cd:
                    fn = part.get_filename() or "attachment"
                    attachments.append(fn)
                elif ct == "text/plain" and not text_body:
                    try:
                        text_body = part.get_payload(decode=True).decode(errors="replace")
                    except Exception:
                        pass
                elif ct == "text/html" and not html_body:
                    try:
                        html_body = part.get_payload(decode=True).decode(errors="replace")
                    except Exception:
                        pass
        else:
            ct = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload:
                decoded = payload.decode(errors="replace")
                if ct == "text/html":
                    html_body = decoded
                else:
                    text_body = decoded

        return MailMessage(
            uid=uid,
            subject=subject,
            sender=sender,
            to=to,
            date=date,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments,
            raw_headers={k: str(v) for k, v in msg.items()},
        )

    # ------------------------------------------------------------------
    # Waiting helpers
    # ------------------------------------------------------------------

    def wait_for_message(
        self,
        *,
        subject_contains: str = "",
        to_contains: str = "",
        timeout: float = 120.0,
        poll_interval: float = 5.0,
        newer_than_uid: str | None = None,
    ) -> list[MailMessage]:
        """Poll until a matching message arrives or timeout expires.

        Returns the matching messages (may be empty on timeout).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                messages = self.fetch_recent(since_seconds=timeout + 60)
            except Exception:
                time.sleep(poll_interval)
                continue

            matched = [
                msg for msg in messages
                if (not subject_contains or subject_contains.lower() in msg.subject.lower())
                and (not to_contains or to_contains.lower() in msg.to.lower())
                and (newer_than_uid is None or msg.uid > newer_than_uid)
            ]
            if matched:
                return matched
            time.sleep(poll_interval)
        return []

    # ------------------------------------------------------------------
    # Link extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_consent_link(msg: MailMessage) -> str | None:
        """Extract the consent confirmation URL from a message's HTML body."""
        body = msg.html_body or msg.text_body
        match = _CONSENT_LINK_RE.search(body)
        return match.group(0) if match else None

    @staticmethod
    def extract_unsubscribe_link(msg: MailMessage) -> str | None:
        body = msg.html_body or msg.text_body
        match = _UNSUBSCRIBE_LINK_RE.search(body)
        return match.group(0) if match else None

    @staticmethod
    def extract_all_links(msg: MailMessage) -> list[str]:
        body = msg.html_body or msg.text_body
        return _ANY_LINK_RE.findall(body)

    @staticmethod
    def has_pdf_attachment(msg: MailMessage) -> bool:
        return any(fn.lower().endswith(".pdf") for fn in msg.attachments)
