from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update

from src.generator.delivery.smtp_providers import resolve_provider_settings
from src.infra.db import session_scope
from src.infra.models import SmtpMailbox
from src.jobs.access import read_job_owner
from src.security.credential_vault import CredentialVaultError, decrypt_secret, encrypt_secret
from src.utils.config import settings


@dataclass(frozen=True)
class ResolvedSmtpCredentials:
    email: str
    password: str
    host: str
    port: int
    use_ssl: bool
    use_starttls: bool
    sender_name: str = ""
    mailbox_id: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _public_mailbox(row: SmtpMailbox) -> dict[str, Any]:
    return {
        "id": row.id,
        "provider": row.provider,
        "email": row.email,
        "sender_name": row.sender_name,
        "host": row.host,
        "port": row.port,
        "use_ssl": bool(row.use_ssl),
        "use_starttls": bool(row.use_starttls),
        "status": row.status,
        "last_error": row.last_error or "",
        "is_default": bool(row.is_default),
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
        "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else "",
    }


def _credentials_from_row(row: SmtpMailbox) -> ResolvedSmtpCredentials:
    return ResolvedSmtpCredentials(
        email=row.email,
        password=decrypt_secret(row.password_encrypted),
        host=row.host,
        port=int(row.port),
        use_ssl=bool(row.use_ssl),
        use_starttls=bool(row.use_starttls),
        sender_name=row.sender_name or "",
        mailbox_id=row.id,
    )


def _credentials_from_env(sender_email: str | None = None) -> ResolvedSmtpCredentials:
    email = _safe_text(sender_email) or _safe_text(settings.smtp_sender_email)
    password = _safe_text(settings.smtp_sender_password)
    host = _safe_text(settings.smtp_host)
    if not email or not password or not host:
        raise RuntimeError("Не настроены SMTP-учётные данные отправителя.")
    return ResolvedSmtpCredentials(
        email=email,
        password=password,
        host=host,
        port=int(settings.smtp_port or 587),
        use_ssl=bool(settings.smtp_use_ssl),
        use_starttls=bool(settings.smtp_use_starttls),
    )


def list_mailboxes(owner_username: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.execute(
            select(SmtpMailbox)
            .where(SmtpMailbox.owner_username == owner_username)
            .order_by(SmtpMailbox.is_default.desc(), SmtpMailbox.created_at.asc())
        ).scalars().all()
        return [_public_mailbox(row) for row in rows]


def get_mailbox(mailbox_id: str, owner_username: str) -> SmtpMailbox | None:
    with session_scope() as session:
        row = session.get(SmtpMailbox, mailbox_id)
        if row is None or row.owner_username != owner_username:
            return None
        return row


def _clear_default_flag(session, owner_username: str) -> None:
    session.execute(
        update(SmtpMailbox)
        .where(SmtpMailbox.owner_username == owner_username, SmtpMailbox.is_default.is_(True))
        .values(is_default=False, updated_at=_now())
    )


def create_mailbox(
    *,
    owner_username: str,
    provider: str,
    email: str,
    password: str,
    sender_name: str = "",
    host: str = "",
    port: int | None = None,
    use_ssl: bool | None = None,
    use_starttls: bool | None = None,
    make_default: bool = False,
) -> dict[str, Any]:
    preset = resolve_provider_settings(
        provider,
        host=host,
        port=port,
        use_ssl=use_ssl,
        use_starttls=use_starttls,
    )
    safe_email = _safe_text(email).lower()
    if not safe_email:
        raise ValueError("Укажите email почтового ящика.")
    if not _safe_text(password):
        raise ValueError("Укажите пароль или токен приложения.")
    now = _now()
    mailbox_id = str(uuid4())
    with session_scope() as session:
        existing = session.execute(
            select(SmtpMailbox).where(SmtpMailbox.owner_username == owner_username)
        ).scalars().first()
        should_default = make_default or existing is None
        if should_default:
            _clear_default_flag(session, owner_username)
        row = SmtpMailbox(
            id=mailbox_id,
            owner_username=owner_username,
            provider=preset.id,
            email=safe_email,
            sender_name=_safe_text(sender_name),
            host=preset.host,
            port=preset.port,
            use_ssl=preset.use_ssl,
            use_starttls=preset.use_starttls,
            password_encrypted=encrypt_secret(password),
            status="active",
            last_error=None,
            is_default=should_default,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return _public_mailbox(row)


def update_mailbox(
    mailbox_id: str,
    *,
    owner_username: str,
    provider: str | None = None,
    email: str | None = None,
    password: str | None = None,
    sender_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    use_ssl: bool | None = None,
    use_starttls: bool | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(SmtpMailbox, mailbox_id)
        if row is None or row.owner_username != owner_username:
            raise LookupError("SMTP-ящик не найден.")
        provider_id = _safe_text(provider) or row.provider
        preset = resolve_provider_settings(
            provider_id,
            host=_safe_text(host) or row.host,
            port=port if port is not None else row.port,
            use_ssl=use_ssl if use_ssl is not None else row.use_ssl,
            use_starttls=use_starttls if use_starttls is not None else row.use_starttls,
        )
        if email is not None:
            safe_email = _safe_text(email).lower()
            if not safe_email:
                raise ValueError("Укажите email почтового ящика.")
            row.email = safe_email
        if password:
            row.password_encrypted = encrypt_secret(password)
        if sender_name is not None:
            row.sender_name = _safe_text(sender_name)
        row.provider = preset.id
        row.host = preset.host
        row.port = preset.port
        row.use_ssl = preset.use_ssl
        row.use_starttls = preset.use_starttls
        row.updated_at = _now()
        row.status = "active"
        row.last_error = None
        session.flush()
        return _public_mailbox(row)


def delete_mailbox(mailbox_id: str, *, owner_username: str) -> None:
    with session_scope() as session:
        row = session.get(SmtpMailbox, mailbox_id)
        if row is None or row.owner_username != owner_username:
            raise LookupError("SMTP-ящик не найден.")
        was_default = bool(row.is_default)
        session.delete(row)
        session.flush()
        if was_default:
            replacement = session.execute(
                select(SmtpMailbox)
                .where(SmtpMailbox.owner_username == owner_username)
                .order_by(SmtpMailbox.created_at.asc())
                .limit(1)
            ).scalar_one_or_none()
            if replacement is not None:
                replacement.is_default = True
                replacement.updated_at = _now()


def set_default_mailbox(mailbox_id: str, *, owner_username: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(SmtpMailbox, mailbox_id)
        if row is None or row.owner_username != owner_username:
            raise LookupError("SMTP-ящик не найден.")
        _clear_default_flag(session, owner_username)
        row.is_default = True
        row.updated_at = _now()
        session.flush()
        return _public_mailbox(row)


def mark_mailbox_status(
    mailbox_id: str | None,
    *,
    status: str,
    last_error: str = "",
) -> None:
    if not mailbox_id:
        return
    with session_scope() as session:
        row = session.get(SmtpMailbox, mailbox_id)
        if row is None:
            return
        row.status = _safe_text(status) or row.status
        row.last_error = _safe_text(last_error) or None
        row.updated_at = _now()


def resolve_smtp_credentials(
    *,
    mailbox_id: str | None = None,
    sender_email: str | None = None,
    owner_username: str | None = None,
    job_id: str | None = None,
) -> ResolvedSmtpCredentials:
    owner = _safe_text(owner_username)
    if not owner and job_id:
        owner = _safe_text(read_job_owner(job_id).get("owner_username"))
    mailbox_key = _safe_text(mailbox_id)
    if mailbox_key and owner:
        with session_scope() as session:
            row = session.get(SmtpMailbox, mailbox_key)
            if row is not None and row.owner_username == owner:
                if row.status == "auth_failed":
                    raise RuntimeError(row.last_error or "SMTP-ящик недоступен: ошибка авторизации.")
                return _credentials_from_row(row)
    if owner:
        with session_scope() as session:
            row = session.execute(
                select(SmtpMailbox)
                .where(
                    SmtpMailbox.owner_username == owner,
                    SmtpMailbox.is_default.is_(True),
                    SmtpMailbox.status != "auth_failed",
                )
                .limit(1)
            ).scalar_one_or_none()
            if row is not None:
                return _credentials_from_row(row)
            if _safe_text(sender_email):
                row = session.execute(
                    select(SmtpMailbox)
                    .where(
                        SmtpMailbox.owner_username == owner,
                        SmtpMailbox.email == _safe_text(sender_email).lower(),
                        SmtpMailbox.status != "auth_failed",
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if row is not None:
                    return _credentials_from_row(row)
    return _credentials_from_env(sender_email)


def humanize_smtp_error(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "Неверный логин или пароль SMTP. Проверьте пароль приложения и настройки провайдера."
    if isinstance(exc, smtplib.SMTPConnectError):
        return "Не удалось подключиться к SMTP-серверу. Проверьте host, порт и шифрование."
    if isinstance(exc, CredentialVaultError):
        return str(exc)
    text = _safe_text(exc)
    if "timed out" in text.lower():
        return "SMTP-сервер не ответил вовремя."
    return text or "Не удалось проверить SMTP-подключение."


def verify_smtp_credentials(credentials: ResolvedSmtpCredentials) -> None:
    if credentials.use_ssl:
        with smtplib.SMTP_SSL(credentials.host, credentials.port, timeout=30) as server:
            server.login(credentials.email, credentials.password)
        return
    with smtplib.SMTP(credentials.host, credentials.port, timeout=30) as server:
        server.ehlo()
        if credentials.use_starttls:
            server.starttls()
            server.ehlo()
        server.login(credentials.email, credentials.password)


def send_test_email(credentials: ResolvedSmtpCredentials, *, recipient: str | None = None) -> None:
    verify_smtp_credentials(credentials)
    target = _safe_text(recipient) or credentials.email
    message = EmailMessage()
    sender_label = credentials.sender_name or credentials.email
    message["Subject"] = "Проверка SMTP-подключения"
    message["From"] = f"{sender_label} <{credentials.email}>" if sender_label else credentials.email
    message["To"] = target
    message.set_content("Тестовое письмо от mailing-agent. SMTP-подключение работает.")
    if credentials.use_ssl:
        with smtplib.SMTP_SSL(credentials.host, credentials.port, timeout=30) as server:
            server.login(credentials.email, credentials.password)
            server.send_message(message)
        return
    with smtplib.SMTP(credentials.host, credentials.port, timeout=30) as server:
        server.ehlo()
        if credentials.use_starttls:
            server.starttls()
            server.ehlo()
        server.login(credentials.email, credentials.password)
        server.send_message(message)
