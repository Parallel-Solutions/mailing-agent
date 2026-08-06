"""Store SMTP messages in the sender mailbox through IMAP.

SMTP delivery and IMAP archiving intentionally have independent outcomes: once an
SMTP server accepts a message, an IMAP failure must never make the caller resend
that message to the recipient.
"""

from __future__ import annotations

import base64
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from src.generator.delivery.smtp_providers import PROVIDER_PRESETS
from src.infra.db import session_scope
from src.infra.models import SmtpMailbox, SmtpSentCopy
from src.security.credential_vault import decrypt_secret
from src.utils.logger import logger


@dataclass(frozen=True)
class ResolvedImapCredentials:
    connection_id: str
    email: str
    username: str
    password: str
    host: str
    port: int
    use_ssl: bool
    use_starttls: bool
    sent_folder: str
    auth_method: str
    save_sent_copy: bool


@dataclass(frozen=True)
class SentCopyResult:
    status: str
    folder: str = ""
    uid: str = ""
    error: str = ""

    @property
    def saved(self) -> bool:
        return self.status in {"archived", "already_present"}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _imap_utf7_decode(value: str) -> str:
    def decode_match(match: re.Match[str]) -> str:
        chunk = match.group(1)
        if chunk == "-":
            return "&"
        payload = chunk[:-1].replace(",", "/")
        padding = "=" * ((4 - len(payload) % 4) % 4)
        raw = base64.b64decode(payload + padding)
        return raw.decode("utf-16-be", errors="strict")

    return re.sub(r"&([A-Za-z0-9+,]*-)", decode_match, value)


def _imap_utf7_encode(value: str) -> str:
    result: list[str] = []
    buffer = ""

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        encoded = base64.b64encode(buffer.encode("utf-16-be")).decode("ascii").rstrip("=")
        result.append("&" + encoded.replace("/", ",") + "-")
        buffer = ""

    for char in value:
        code = ord(char)
        if 0x20 <= code <= 0x7E and char != "&":
            flush_buffer()
            result.append(char)
        elif char == "&":
            flush_buffer()
            result.append("&-")
        else:
            buffer += char
    flush_buffer()
    return "".join(result)


def _extract_mailbox_name(line: bytes | str) -> tuple[str, str]:
    text = line.decode("ascii", errors="ignore") if isinstance(line, bytes) else str(line)
    match = re.match(r'^\((?P<flags>.*?)\)\s+(?P<delimiter>"[^"]*"|NIL)\s+(?P<name>.+)$', text.strip())
    if not match:
        return "", ""
    flags = _safe_text(match.group("flags"))
    name = _safe_text(match.group("name"))
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1].replace(r'\"', '"').replace(r"\\", "\\")
    try:
        return flags, _imap_utf7_decode(name)
    except (UnicodeError, ValueError):
        return flags, name


def _sent_folder_candidates(client: imaplib.IMAP4, configured_folder: str) -> list[str]:
    discovered: list[str] = []
    try:
        status, mailboxes = client.list()
    except Exception:
        status, mailboxes = "NO", []
    if status == "OK":
        for item in mailboxes or []:
            if item is None:
                continue
            flags, name = _extract_mailbox_name(item)
            if not name:
                continue
            haystack = f"{flags} {name}".casefold()
            if "\\sent" in haystack or "sent" in haystack or "отправ" in haystack:
                discovered.append(name)

    candidates = [
        configured_folder,
        *discovered,
        "Sent",
        "Sent Messages",
        "Sent Items",
        "Отправленные",
    ]
    unique: list[str] = []
    for candidate in candidates:
        safe_candidate = _safe_text(candidate)
        if safe_candidate and safe_candidate not in unique:
            unique.append(safe_candidate)
    return unique


def resolve_imap_credentials(*, mailbox_id: str, owner_username: str) -> ResolvedImapCredentials:
    from src.generator.delivery.smtp_mailboxes import resolve_smtp_credentials

    with session_scope() as session:
        row = session.get(SmtpMailbox, mailbox_id)
        if row is None or row.owner_username != owner_username:
            raise LookupError("IMAP mailbox not found.")
        provider = PROVIDER_PRESETS.get(row.provider, PROVIDER_PRESETS["custom"])
        connection_id = row.id
        email = row.email
        save_sent_copy = bool(row.save_sent_copy)
        host = _safe_text(row.imap_host) or provider.imap_host
        port = int(row.imap_port or provider.imap_port or 993)
        use_ssl = bool(row.imap_use_ssl)
        use_starttls = bool(row.imap_use_starttls)
        username = _safe_text(row.imap_username)
        sent_folder = _safe_text(row.imap_sent_folder) or provider.imap_sent_folder
        separate_password = (
            decrypt_secret(row.imap_password_encrypted)
            if _safe_text(row.imap_password_encrypted)
            else ""
        )

    smtp_credentials = resolve_smtp_credentials(
        mailbox_id=mailbox_id,
        owner_username=owner_username,
    )
    password = separate_password or smtp_credentials.password
    auth_method = "password" if separate_password else smtp_credentials.auth_method
    return ResolvedImapCredentials(
        connection_id=connection_id,
        email=email,
        username=username or smtp_credentials.smtp_username or email,
        password=password,
        host=host,
        port=port,
        use_ssl=use_ssl,
        use_starttls=use_starttls,
        sent_folder=sent_folder,
        auth_method=auth_method,
        save_sent_copy=save_sent_copy,
    )


def _open_imap_connection(credentials: ResolvedImapCredentials) -> imaplib.IMAP4:
    if credentials.use_ssl:
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
            credentials.host,
            credentials.port,
            timeout=30,
        )
    else:
        client = imaplib.IMAP4(credentials.host, credentials.port, timeout=30)
        if credentials.use_starttls:
            client.starttls()

    if credentials.auth_method == "oauth":
        auth = (
            f"user={credentials.username}\x01"
            f"auth=Bearer {credentials.password}\x01\x01"
        ).encode("utf-8")
        client.authenticate("XOAUTH2", lambda _challenge: auth)
    else:
        client.login(credentials.username, credentials.password)
    return client


def _quote_search_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', r'\"') + '"'


def _find_message_uid(client: imaplib.IMAP4, folder: str, message_id: str) -> str:
    status, _ = client.select(_imap_utf7_encode(folder), readonly=True)
    if status != "OK":
        return ""
    status, data = client.uid(
        "SEARCH",
        None,
        "HEADER",
        "Message-ID",
        _quote_search_value(message_id),
    )
    if status != "OK" or not data:
        return ""
    values = data[0].decode("ascii", errors="ignore").split() if data[0] else []
    return values[-1] if values else ""


def _extract_append_uid(data: list[bytes | None] | None) -> str:
    text = " ".join(
        item.decode("ascii", errors="ignore")
        for item in (data or [])
        if isinstance(item, bytes)
    )
    match = re.search(r"APPENDUID\s+\d+\s+(\d+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _append_once(
    credentials: ResolvedImapCredentials,
    *,
    raw_message: bytes,
    message_id: str,
) -> SentCopyResult:
    client = _open_imap_connection(credentials)
    try:
        candidates = _sent_folder_candidates(client, credentials.sent_folder)
        accessible: list[str] = []
        for folder in candidates:
            try:
                uid = _find_message_uid(client, folder, message_id)
            except imaplib.IMAP4.error:
                continue
            if uid:
                return SentCopyResult(status="already_present", folder=folder, uid=uid)
            status, _ = client.select(_imap_utf7_encode(folder), readonly=True)
            if status == "OK":
                accessible.append(folder)

        if not accessible:
            raise RuntimeError("IMAP-папка «Отправленные» не найдена.")

        internal_date = imaplib.Time2Internaldate(datetime.now().astimezone())
        errors: list[str] = []
        for folder in accessible:
            try:
                status, data = client.append(
                    _imap_utf7_encode(folder),
                    "(\\Seen)",
                    internal_date,
                    raw_message,
                )
            except imaplib.IMAP4.error as exc:
                errors.append(f"{folder}: {exc}")
                continue
            if status == "OK":
                uid = _extract_append_uid(data)
                if not uid:
                    try:
                        uid = _find_message_uid(client, folder, message_id)
                    except imaplib.IMAP4.error:
                        uid = ""
                return SentCopyResult(status="archived", folder=folder, uid=uid)
            errors.append(f"{folder}: APPEND {status}")
        raise RuntimeError("; ".join(errors) or "IMAP APPEND failed.")
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _record_outcome(
    *,
    connection_id: str,
    message_id: str,
    recipient: str,
    result: SentCopyResult,
) -> None:
    try:
        with session_scope() as session:
            row = session.scalar(
                select(SmtpSentCopy).where(
                    SmtpSentCopy.connection_id == connection_id,
                    SmtpSentCopy.message_id == message_id,
                )
            )
            if row is None:
                row = SmtpSentCopy(
                    connection_id=connection_id,
                    message_id=message_id,
                    recipient=recipient,
                )
                session.add(row)
            row.recipient = recipient
            row.status = result.status
            row.folder = result.folder or None
            row.uid = result.uid or None
            row.error = result.error or None
            row.updated_at = datetime.now().astimezone()
    except Exception:
        logger.exception(
            "smtp_sent_copy_outcome_record_failed",
            connection_id=connection_id,
            message_id=message_id,
        )


def archive_sent_copy(
    *,
    mailbox_id: str,
    owner_username: str,
    recipient: str,
    raw_message: bytes,
    message_id: str,
    max_attempts: int = 2,
) -> SentCopyResult:
    """Archive a message without ever changing the SMTP delivery outcome."""

    try:
        credentials = resolve_imap_credentials(
            mailbox_id=mailbox_id,
            owner_username=owner_username,
        )
    except Exception as exc:
        result = SentCopyResult(status="failed", error=_safe_text(exc))
        _record_outcome(
            connection_id=mailbox_id,
            message_id=message_id,
            recipient=recipient,
            result=result,
        )
        return result

    if not credentials.save_sent_copy:
        result = SentCopyResult(status="disabled")
    elif not credentials.host:
        result = SentCopyResult(
            status="not_configured",
            error="Для подключения не указан IMAP-сервер.",
        )
    else:
        errors: list[str] = []
        result = SentCopyResult(status="failed")
        for _attempt in range(max(1, int(max_attempts))):
            try:
                result = _append_once(
                    credentials,
                    raw_message=raw_message,
                    message_id=message_id,
                )
                break
            except Exception as exc:
                errors.append(_safe_text(exc) or exc.__class__.__name__)
        if not result.saved:
            result = SentCopyResult(
                status="failed",
                error=errors[-1] if errors else "Не удалось сохранить письмо через IMAP.",
            )

    _record_outcome(
        connection_id=mailbox_id,
        message_id=message_id,
        recipient=recipient,
        result=result,
    )
    if result.status == "failed":
        logger.warning(
            "smtp_sent_copy_failed",
            connection_id=mailbox_id,
            message_id=message_id,
            error=result.error,
        )
    return result


def verify_imap_connection(*, mailbox_id: str, owner_username: str) -> dict[str, Any]:
    credentials = resolve_imap_credentials(
        mailbox_id=mailbox_id,
        owner_username=owner_username,
    )
    if not credentials.save_sent_copy:
        return {"status": "disabled", "folder": ""}
    if not credentials.host:
        raise ValueError("Для подключения не указан IMAP-сервер.")

    client = _open_imap_connection(credentials)
    try:
        for folder in _sent_folder_candidates(client, credentials.sent_folder):
            status, _ = client.select(_imap_utf7_encode(folder), readonly=True)
            if status == "OK":
                return {"status": "ok", "folder": folder}
    finally:
        try:
            client.logout()
        except Exception:
            pass
    raise ValueError("IMAP-папка «Отправленные» не найдена.")
