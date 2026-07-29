from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update

from src.generator.delivery.smtp_oauth import (
    OAuthTokens,
    build_xoauth2_string,
    decrypt_oauth_tokens,
    encrypt_oauth_tokens,
    refresh_oauth_tokens,
)
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
    auth_method: str = "password"
    oauth_provider: str | None = None
    oauth_tokens: OAuthTokens | None = None
    smtp_username: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_smtp_secret(value: Any) -> str:
    """Normalize SMTP app passwords / secrets (strip all whitespace).

    Google app passwords are often pasted as ``xxxx xxxx xxxx xxxx``; SMTP login
    accepts them with or without spaces, but we store and authenticate on the
    compact 16-character form.
    """
    return "".join(_safe_text(value).split())


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
        "auth_method": row.auth_method or "password",
        "oauth_provider": row.oauth_provider or "",
        "smtp_username": row.smtp_username or "",
        "status": row.status,
        "last_error": row.last_error or "",
        "is_default": bool(row.is_default),
        "max_per_hour": int(row.max_per_hour or 0),
        "max_per_day": int(row.max_per_day or 0),
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
        "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else "",
    }


def build_oauth_credentials(
    *,
    email: str,
    provider: str,
    tokens: OAuthTokens,
    host: str,
    port: int,
    use_ssl: bool,
    use_starttls: bool,
    sender_name: str = "",
    smtp_username: str | None = None,
    mailbox_id: str | None = None,
) -> ResolvedSmtpCredentials:
    return ResolvedSmtpCredentials(
        email=email,
        password=tokens.access_token,
        host=host,
        port=port,
        use_ssl=use_ssl,
        use_starttls=use_starttls,
        sender_name=sender_name,
        auth_method="oauth",
        oauth_provider=provider,
        oauth_tokens=tokens,
        smtp_username=str(smtp_username or email),
        mailbox_id=mailbox_id,
    )


def _credentials_from_row(row: SmtpMailbox) -> ResolvedSmtpCredentials:
    auth_method = _safe_text(row.auth_method) or "password"
    smtp_username = _safe_text(row.smtp_username) or row.email
    if auth_method == "oauth" and row.oauth_tokens_encrypted:
        tokens = decrypt_oauth_tokens(row.oauth_tokens_encrypted)
        return build_oauth_credentials(
            email=row.email,
            provider=_safe_text(row.oauth_provider),
            tokens=tokens,
            host=row.host,
            port=int(row.port),
            use_ssl=bool(row.use_ssl),
            use_starttls=bool(row.use_starttls),
            sender_name=row.sender_name or "",
            smtp_username=smtp_username,
            mailbox_id=row.id,
        )
    return ResolvedSmtpCredentials(
        email=row.email,
        password=decrypt_secret(row.password_encrypted),
        host=row.host,
        port=int(row.port),
        use_ssl=bool(row.use_ssl),
        use_starttls=bool(row.use_starttls),
        sender_name=row.sender_name or "",
        mailbox_id=row.id,
        auth_method="password",
        smtp_username=smtp_username,
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
        smtp_username=email,
    )


def list_mailboxes(owner_username: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.execute(
            select(SmtpMailbox)
            .where(
                SmtpMailbox.owner_username == owner_username,
                SmtpMailbox.provider.notin_(["rusender", "mailopost"]),
            )
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
    password: str = "",
    sender_name: str = "",
    host: str = "",
    port: int | None = None,
    use_ssl: bool | None = None,
    use_starttls: bool | None = None,
    make_default: bool = False,
    auth_method: str = "password",
    oauth_provider: str | None = None,
    oauth_tokens: OAuthTokens | None = None,
    smtp_username: str | None = None,
    max_per_hour: int = 0,
    max_per_day: int = 0,
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
    normalized_auth = _safe_text(auth_method) or "password"
    safe_oauth_provider = _safe_text(oauth_provider) or None
    safe_username = _safe_text(smtp_username) or safe_email
    if normalized_auth == "oauth":
        if oauth_tokens is None or not oauth_tokens.access_token:
            raise ValueError("Для OAuth-ящика нужен access token.")
        password_encrypted = ""
        oauth_tokens_encrypted = encrypt_oauth_tokens(oauth_tokens)
    else:
        safe_password = normalize_smtp_secret(password)
        if not safe_password:
            raise ValueError("Укажите пароль или токен приложения.")
        password_encrypted = encrypt_secret(safe_password)
        oauth_tokens_encrypted = None
        safe_oauth_provider = None
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
            auth_method=normalized_auth,
            oauth_provider=safe_oauth_provider,
            oauth_tokens_encrypted=oauth_tokens_encrypted,
            smtp_username=safe_username,
            password_encrypted=password_encrypted,
            status="active",
            last_error=None,
            is_default=should_default,
            max_per_hour=max(0, int(max_per_hour or 0)),
            max_per_day=max(0, int(max_per_day or 0)),
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
    auth_method: str | None = None,
    oauth_provider: str | None = None,
    oauth_tokens: OAuthTokens | None = None,
    smtp_username: str | None = None,
    max_per_hour: int | None = None,
    max_per_day: int | None = None,
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
        if auth_method is not None:
            row.auth_method = _safe_text(auth_method) or "password"
        if oauth_provider is not None:
            row.oauth_provider = _safe_text(oauth_provider) or None
        if oauth_tokens is not None:
            row.oauth_tokens_encrypted = encrypt_oauth_tokens(oauth_tokens)
            row.auth_method = "oauth"
        if password:
            safe_password = normalize_smtp_secret(password)
            if not safe_password:
                raise ValueError("Укажите пароль или токен приложения.")
            row.password_encrypted = encrypt_secret(safe_password)
            row.auth_method = "password"
            row.oauth_provider = None
            row.oauth_tokens_encrypted = None
        if sender_name is not None:
            row.sender_name = _safe_text(sender_name)
        if smtp_username is not None:
            row.smtp_username = _safe_text(smtp_username) or row.email
        if max_per_hour is not None:
            row.max_per_hour = max(0, int(max_per_hour))
        if max_per_day is not None:
            row.max_per_day = max(0, int(max_per_day))
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
            # TODO(security): replace this temporary global-use path with
            # organization-owned credentials and explicit membership checks.
            from src.security.company_access import TEMPORARY_GLOBAL_ORGANIZATION_ACCESS

            can_use_mailbox = row is not None and (
                row.owner_username == owner or TEMPORARY_GLOBAL_ORGANIZATION_ACCESS
            )
            if can_use_mailbox:
                if row.status == "auth_failed":
                    raise RuntimeError(row.last_error or "SMTP-ящик недоступен: ошибка авторизации.")
                credentials = _credentials_from_row(row)
                return _ensure_fresh_oauth_credentials(credentials, row)
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
                credentials = _credentials_from_row(row)
                return _ensure_fresh_oauth_credentials(credentials, row)
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
                    credentials = _credentials_from_row(row)
                    return _ensure_fresh_oauth_credentials(credentials, row)
    return _credentials_from_env(sender_email)


def _ensure_fresh_oauth_credentials(
    credentials: ResolvedSmtpCredentials,
    row: SmtpMailbox,
) -> ResolvedSmtpCredentials:
    if credentials.auth_method != "oauth" or credentials.oauth_tokens is None:
        return credentials
    if not credentials.oauth_tokens.refresh_token:
        return credentials
    try:
        refreshed = refresh_oauth_tokens(
            provider=_safe_text(credentials.oauth_provider),
            refresh_token=credentials.oauth_tokens.refresh_token,
        )
    except Exception:
        return credentials
    with session_scope() as session:
        db_row = session.get(SmtpMailbox, row.id)
        if db_row is not None:
            db_row.oauth_tokens_encrypted = encrypt_oauth_tokens(refreshed)
            db_row.updated_at = _now()
    return build_oauth_credentials(
        email=credentials.email,
        provider=_safe_text(credentials.oauth_provider),
        tokens=refreshed,
        host=credentials.host,
        port=credentials.port,
        use_ssl=credentials.use_ssl,
        use_starttls=credentials.use_starttls,
        sender_name=credentials.sender_name,
        smtp_username=credentials.smtp_username or credentials.email,
        mailbox_id=credentials.mailbox_id,
    )


def _humanize_smtp_auth_error(
    *,
    provider: str = "",
    host: str = "",
    email: str = "",
) -> str:
    normalized_provider = _safe_text(provider).lower()
    normalized_host = _safe_text(host).lower()
    normalized_email = _safe_text(email).lower()
    if normalized_provider == "mailru" or "mail.ru" in normalized_host:
        return (
            "Неверный логин или пароль SMTP. "
            "Для Почты Mail нужен пароль для внешнего приложения (не обычный пароль от входа в почту). "
            "Создайте его в настройках безопасности: https://help.mail.ru/mail/security/protection/external"
        )
    if normalized_provider == "yandex" or "yandex" in normalized_host:
        return (
            "Неверный логин или пароль SMTP. "
            "Для Яндекса нужен пароль приложения (не обычный пароль от входа в почту). "
            "Создайте его здесь: https://id.yandex.ru/security/app-passwords"
        )
    if (
        normalized_provider == "gmail"
        or "gmail.com" in normalized_host
        or normalized_email.endswith("@gmail.com")
        or normalized_email.endswith("@googlemail.com")
    ):
        return (
            "Неверный логин или пароль SMTP. "
            "Для Gmail нужен пароль приложения (не обычный пароль Google), "
            "его можно вставить без пробелов: https://myaccount.google.com/apppasswords"
        )
    return (
        "Неверный логин или пароль SMTP. "
        "Если включена двухфакторная аутентификация, используйте пароль приложения."
    )


def humanize_smtp_error(
    exc: Exception,
    *,
    provider: str = "",
    host: str = "",
    email: str = "",
) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return _humanize_smtp_auth_error(provider=provider, host=host, email=email)
    if isinstance(exc, smtplib.SMTPConnectError):
        return "Не удалось подключиться к SMTP-серверу. Проверьте host, порт и шифрование."
    if isinstance(exc, CredentialVaultError):
        return str(exc)
    if isinstance(exc, (TimeoutError,)):
        return (
            "SMTP-сервер не ответил вовремя. "
            "Проверьте сеть и исходящий доступ к портам 465/587 "
            "(часто блокирует провайдер или файрвол — и с хоста, и из контейнера)."
        )
    if isinstance(exc, OSError):
        text = _safe_text(exc).lower()
        if "network is unreachable" in text or getattr(exc, "errno", None) == 101:
            return (
                "Нет доступа к SMTP-серверу (порты 465/587). "
                "Проверьте файрвол, VPN и блокировку исходящего SMTP у провайдера — "
                "в том числе с хоста, не только из контейнера."
            )
        if "timed out" in text:
            return (
                "SMTP-сервер не ответил вовремя. "
                "Проверьте сеть и исходящий доступ к портам 465/587 "
                "(часто блокирует провайдер или файрвол — и с хоста, и из контейнера)."
            )
    text = _safe_text(exc)
    if "timed out" in text.lower():
        return (
            "SMTP-сервер не ответил вовремя. "
            "Проверьте сеть и исходящий доступ к портам 465/587 "
            "(часто блокирует провайдер или файрвол — и с хоста, и из контейнера)."
        )
    if "network is unreachable" in text.lower():
        return (
            "Нет доступа к SMTP-серверу (порты 465/587). "
            "Проверьте файрвол, VPN и блокировку исходящего SMTP у провайдера — "
            "в том числе с хоста, не только из контейнера."
        )
    return text or "Не удалось проверить SMTP-подключение."


def _login_smtp(server: smtplib.SMTP, credentials: ResolvedSmtpCredentials) -> None:
    username = _safe_text(credentials.smtp_username) or credentials.email
    if credentials.auth_method == "oauth":
        auth_string = build_xoauth2_string(username, credentials.password)
        code, response = server.docmd("AUTH", "XOAUTH2 " + auth_string)
        if code != 235:
            detail = response.decode("utf-8", errors="replace") if isinstance(response, bytes) else str(response)
            raise smtplib.SMTPAuthenticationError(code, detail)
        return
    server.login(username, normalize_smtp_secret(credentials.password))


def _open_smtp_connection(credentials: ResolvedSmtpCredentials) -> smtplib.SMTP:
    if credentials.use_ssl:
        server = smtplib.SMTP_SSL(credentials.host, credentials.port, timeout=30)
        _login_smtp(server, credentials)
        return server
    server = smtplib.SMTP(credentials.host, credentials.port, timeout=30)
    server.ehlo()
    if credentials.use_starttls:
        server.starttls()
        server.ehlo()
    _login_smtp(server, credentials)
    return server


def verify_smtp_credentials(credentials: ResolvedSmtpCredentials) -> None:
    server = _open_smtp_connection(credentials)
    try:
        server.noop()
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            server.close()


def send_test_email(
    credentials: ResolvedSmtpCredentials,
    *,
    recipient: str | None = None,
    include_sample_attachment: bool = False,
) -> None:
    target = _safe_text(recipient) or credentials.email
    from src.generator.delivery.email_validation import validate_configured_email_address

    email_validation = validate_configured_email_address(target)
    if not email_validation.is_valid:
        raise ValueError(email_validation.reason or "Email не прошёл проверку SMTP.BZ.")
    target = email_validation.normalized_email
    message = EmailMessage()
    sender_label = credentials.sender_name or credentials.email
    message["Subject"] = "Проверка SMTP-подключения"
    message["From"] = f"{sender_label} <{credentials.email}>" if sender_label else credentials.email
    message["To"] = target
    message.set_content("Тестовое письмо от mailing-agent. SMTP-подключение работает.")
    if include_sample_attachment:
        # Minimal valid-looking PDF bytes for SMTP attachment checks (Mailpit / clients).
        sample_pdf = (
            b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
            b"mailing-agent-e2e-sample-attachment\n"
        )
        message.add_attachment(
            sample_pdf,
            maintype="application",
            subtype="pdf",
            filename="e2e-sample.pdf",
        )
    server = _open_smtp_connection(credentials)
    try:
        server.send_message(message)
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            server.close()


def verify_and_mark_mailbox(
    credentials: ResolvedSmtpCredentials,
    *,
    mailbox_id: str | None = None,
    send_test: bool = False,
    recipient: str | None = None,
    include_sample_attachment: bool = False,
) -> None:
    try:
        if send_test:
            send_test_email(
                credentials,
                recipient=recipient,
                include_sample_attachment=include_sample_attachment,
            )
        else:
            verify_smtp_credentials(credentials)
    except smtplib.SMTPAuthenticationError as exc:
        if mailbox_id:
            mark_mailbox_status(mailbox_id, status="auth_failed", last_error=humanize_smtp_error(exc))
        raise
    except Exception:
        raise
    if mailbox_id:
        mark_mailbox_status(mailbox_id, status="active", last_error="")
