"""User-owned delivery connections for SMTP, RuSender and MailoPost.

API-provider credentials reuse the encrypted secret storage of ``smtp_mailboxes``
for backwards compatibility with existing campaign records.  The public API
never exposes encrypted credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update

from src.generator.delivery.smtp_mailboxes import (
    ResolvedSmtpCredentials,
    create_mailbox,
    delete_mailbox,
    humanize_smtp_error,
    mark_mailbox_status,
    normalize_smtp_secret,
    resolve_smtp_credentials,
    update_mailbox,
    verify_and_mark_mailbox,
    verify_smtp_credentials,
)
from src.generator.delivery.channel_guard import channel_state_blocks_send
from src.generator.delivery.smtp_oauth import OAuthTokens
from src.infra.db import session_scope
from src.infra.models import Campaign, Company, SmtpMailbox
from src.security.company_access import (
    apply_owner_filter,
    can_access_owner,
)
from src.security.credential_vault import decrypt_secret, encrypt_secret
from src.utils.config import settings


API_PROVIDERS = {"rusender", "mailopost"}
SUPPORTED_TRANSPORTS = {"smtp", *API_PROVIDERS}
MAILRU_HOST = "smtp.mail.ru"
MAILRU_PORT = 465

_CONNECTION_VISIBILITY_UNSET = object()


def _effective_connection_visibility(
    owner_username: str,
    visible_owners: Any,
) -> frozenset[str] | None:
    if visible_owners is _CONNECTION_VISIBILITY_UNSET:
        return frozenset({owner_username})
    return visible_owners



@dataclass(frozen=True)
class ResolvedConnection:
    id: str
    transport: str
    email: str
    sender_name: str
    secret: str
    api_base_url: str
    sending_key_id: int | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _rate_limit_value(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _optional_rate_limit(data: dict[str, Any], key: str) -> int | None:
    if key not in data or data.get(key) is None:
        return None
    return _rate_limit_value(data.get(key))


def _optional_sending_key_id(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("ID ключа отправки RuSender должен быть целым числом.") from exc
    if parsed <= 0:
        raise ValueError("ID ключа отправки RuSender должен быть больше нуля.")
    return parsed


def _validate_rusender_sending_key_id(sending_key_id: int | None) -> None:
    if sending_key_id is None:
        raise ValueError("Укажите ID ключа отправки RuSender.")


def _company_name_for_sender(owner_username: str, *, campaign: Campaign | None = None) -> str:
    if campaign is not None:
        from src.campaigns.document_number_service import resolve_campaign_company_id

        company_id = resolve_campaign_company_id(campaign)
        if company_id:
            with session_scope() as session:
                company = session.get(Company, company_id)
                if company is not None:
                    return _safe_text(company.name)
    else:
        from src.campaigns.company_service import get_company_for_user

        company = get_company_for_user(owner_username)
        if company:
            return _safe_text(company.get("name"))
    return ""


def resolve_sender_name(
    owner_username: str,
    *,
    campaign: Campaign | None = None,
    fallback: Any = "",
) -> str:
    """Prefer the campaign or user company name; fall back to stored connection name."""
    company_name = _company_name_for_sender(owner_username, campaign=campaign)
    if company_name:
        return company_name
    return _safe_text(fallback)


def _normalize_mailru_email(value: Any) -> str:
    email = _safe_text(value).lower()
    local_part, separator, domain = email.rpartition("@")
    if not separator or not local_part or not domain or email.count("@") != 1 or any(char.isspace() for char in email):
        raise ValueError("Укажите корректный email почтового ящика.")
    return email


def _verify_mailru_credentials(
    *,
    email: Any,
    password: Any,
    sender_name: Any = "",
) -> None:
    safe_email = _normalize_mailru_email(email)
    safe_password = normalize_smtp_secret(password)
    if not safe_password:
        raise ValueError("Укажите пароль для внешнего приложения Почты Mail.")
    credentials = ResolvedSmtpCredentials(
        email=safe_email,
        password=safe_password,
        host=MAILRU_HOST,
        port=MAILRU_PORT,
        use_ssl=True,
        use_starttls=False,
        sender_name=_safe_text(sender_name),
        smtp_username=safe_email,
    )
    try:
        verify_smtp_credentials(credentials)
    except Exception as exc:
        message = humanize_smtp_error(
            exc,
            provider="mailru",
            host=MAILRU_HOST,
            email=safe_email,
        )
        raise ValueError(
            "Почта Mail не приняла данные подключения. "
            "Используйте пароль для внешнего приложения и проверьте, что доступ по SMTP включён. "
            f"{message}"
        ) from exc


def connection_transport(row: SmtpMailbox) -> str:
    provider = _safe_text(row.provider).lower()
    return provider if provider in API_PROVIDERS else "smtp"


def _public_connection(row: SmtpMailbox, session: Any | None = None) -> dict[str, Any]:
    from src.generator.delivery.channel_guard import guard_snapshot, refresh_guard_snapshot

    delivery_guard = (
        refresh_guard_snapshot(session, row) if session is not None else guard_snapshot(row)
    )

    transport = connection_transport(row)
    auth_method = _safe_text(row.auth_method) or "password"
    return {
        "id": row.id,
        "transport": transport,
        "provider": transport if transport != "smtp" else row.provider,
        "email": row.email,
        "sender_name": row.sender_name or "",
        "host": row.host if transport == "smtp" else "",
        "port": row.port if transport == "smtp" else None,
        "use_ssl": bool(row.use_ssl) if transport == "smtp" else None,
        "use_starttls": bool(row.use_starttls) if transport == "smtp" else None,
        "save_sent_copy": bool(row.save_sent_copy) if transport == "smtp" else False,
        "imap_host": (row.imap_host or "") if transport == "smtp" else "",
        "imap_port": int(row.imap_port or 993) if transport == "smtp" else None,
        "imap_use_ssl": bool(row.imap_use_ssl) if transport == "smtp" else None,
        "imap_use_starttls": bool(row.imap_use_starttls) if transport == "smtp" else None,
        "imap_username": (row.imap_username or "") if transport == "smtp" else "",
        "imap_sent_folder": (row.imap_sent_folder or "") if transport == "smtp" else "",
        "imap_password_configured": bool(row.imap_password_encrypted) if transport == "smtp" else False,
        "api_base_url": row.host if transport in API_PROVIDERS else "",
        "sending_key_id": row.sending_key_id if transport == "rusender" else None,
        "auth_method": auth_method,
        "oauth_provider": row.oauth_provider or "",
        "status": row.status,
        "last_error": row.last_error or "",
        "is_default": bool(row.is_default),
        "has_secret": (
            transport != "rusender" and (bool(row.password_encrypted) or bool(row.oauth_tokens_encrypted))
        ),
        "max_per_hour": int(row.max_per_hour or 0),
        "max_per_day": int(row.max_per_day or 0),
        "delivery_guard_enabled": bool(row.delivery_guard_enabled),
        "delivery_error_rate_threshold": float(row.delivery_error_rate_threshold or 0.05),
        "delivery_error_window_minutes": int(row.delivery_error_window_minutes or 60),
        "delivery_error_min_samples": int(row.delivery_error_min_samples or 20),
        "delivery_error_critical_count": int(row.delivery_error_critical_count or 0),
        "delivery_error_action": str(row.delivery_error_action or "warmup"),
        "delivery_throttled_max_per_hour": int(row.delivery_throttled_max_per_hour or 50),
        "warmup_recipients": list(row.warmup_recipients or []),
        "warmup_percent_of_errors": int(row.warmup_percent_of_errors or 100),
        "delivery_guard": delivery_guard,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
        "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else "",
    }


def _apply_guard_settings_and_public(connection_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from src.generator.delivery.channel_guard import apply_scoped_guard_settings

    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None:
            raise LookupError("Delivery connection not found.")
        apply_scoped_guard_settings(session, row, data)
        row.updated_at = _now()
        session.flush()
        return _public_connection(row, session)


def list_connections(
    owner_username: str,
    *,
    visible_owners: Any = _CONNECTION_VISIBILITY_UNSET,
) -> list[dict[str, Any]]:
    visible_owners = _effective_connection_visibility(owner_username, visible_owners)
    with session_scope() as session:
        stmt = select(SmtpMailbox).order_by(SmtpMailbox.is_default.desc(), SmtpMailbox.created_at.asc())
        stmt = apply_owner_filter(stmt, SmtpMailbox.owner_username, visible_owners)
        rows = session.execute(stmt).scalars().all()
        return [_public_connection(row, session) for row in rows]


def create_connection(owner_username: str, data: dict[str, Any]) -> dict[str, Any]:
    transport = _safe_text(data.get("transport") or "smtp").lower()
    if transport not in SUPPORTED_TRANSPORTS:
        raise ValueError("Поддерживаются SMTP, RuSender и MailoPost.")
    # Prefer an explicit payload value; otherwise take the user's company name.
    sender_name = _safe_text(data.get("sender_name")) or resolve_sender_name(owner_username, fallback="")
    max_per_hour = _rate_limit_value(data.get("max_per_hour"))
    max_per_day = _rate_limit_value(data.get("max_per_day"))
    if transport == "smtp":
        provider = _safe_text(data.get("provider") or "custom").lower()
        auth_method = _safe_text(data.get("auth_method") or "password").lower() or "password"
        if auth_method == "oauth":
            oauth_provider = _safe_text(data.get("oauth_provider")).lower()
            tokens_raw = data.get("oauth_tokens")
            if oauth_provider not in {"google", "microsoft"}:
                raise ValueError("Для OAuth укажите провайдера google или microsoft.")
            if not isinstance(tokens_raw, dict):
                raise ValueError("Для OAuth передайте oauth_tokens.")
            oauth_tokens = OAuthTokens.from_dict(tokens_raw)
            if not oauth_tokens.access_token:
                raise ValueError("Для OAuth нужен access token.")
            mailbox = create_mailbox(
                owner_username=owner_username,
                provider=provider,
                email=_safe_text(data.get("email")),
                sender_name=sender_name,
                host=_safe_text(data.get("host")),
                port=data.get("port"),
                use_ssl=data.get("use_ssl"),
                use_starttls=data.get("use_starttls"),
                make_default=bool(data.get("make_default")),
                auth_method="oauth",
                oauth_provider=oauth_provider,
                oauth_tokens=oauth_tokens,
                smtp_username=_safe_text(data.get("smtp_username")) or None,
                save_sent_copy=bool(data.get("save_sent_copy", True)),
                imap_host=_safe_text(data.get("imap_host")),
                imap_port=data.get("imap_port"),
                imap_use_ssl=data.get("imap_use_ssl"),
                imap_use_starttls=data.get("imap_use_starttls"),
                imap_username=_safe_text(data.get("imap_username")) or None,
                imap_password=_safe_text(data.get("imap_password")),
                imap_sent_folder=_safe_text(data.get("imap_sent_folder")),
                max_per_hour=max_per_hour,
                max_per_day=max_per_day,
            )
            with session_scope() as session:
                row = session.get(SmtpMailbox, mailbox["id"])
                if row is None:
                    raise LookupError("Подключение не найдено после создания.")
            return _apply_guard_settings_and_public(mailbox["id"], data)
        if provider == "mailru":
            email = _normalize_mailru_email(data.get("email"))
            password = _safe_text(data.get("password"))
            _verify_mailru_credentials(
                email=email,
                password=password,
                sender_name=sender_name,
            )
            mailbox = create_mailbox(
                owner_username=owner_username,
                provider="mailru",
                email=email,
                password=password,
                sender_name=sender_name,
                make_default=bool(data.get("make_default")),
                smtp_username=email,
                save_sent_copy=bool(data.get("save_sent_copy", True)),
                imap_host=_safe_text(data.get("imap_host")),
                imap_port=data.get("imap_port"),
                imap_use_ssl=data.get("imap_use_ssl"),
                imap_use_starttls=data.get("imap_use_starttls"),
                imap_username=_safe_text(data.get("imap_username")) or email,
                imap_password=_safe_text(data.get("imap_password")),
                imap_sent_folder=_safe_text(data.get("imap_sent_folder")),
                max_per_hour=max_per_hour,
                max_per_day=max_per_day,
            )
            return _apply_guard_settings_and_public(mailbox["id"], data)
        mailbox = create_mailbox(
            owner_username=owner_username,
            provider=provider,
            email=_safe_text(data.get("email")),
            password=normalize_smtp_secret(data.get("password")),
            sender_name=sender_name,
            host=_safe_text(data.get("host")),
            port=data.get("port"),
            use_ssl=data.get("use_ssl"),
            use_starttls=data.get("use_starttls"),
            make_default=bool(data.get("make_default")),
            smtp_username=_safe_text(data.get("smtp_username")) or None,
            save_sent_copy=bool(data.get("save_sent_copy", True)),
            imap_host=_safe_text(data.get("imap_host")),
            imap_port=data.get("imap_port"),
            imap_use_ssl=data.get("imap_use_ssl"),
            imap_use_starttls=data.get("imap_use_starttls"),
            imap_username=_safe_text(data.get("imap_username")) or None,
            imap_password=_safe_text(data.get("imap_password")),
            imap_sent_folder=_safe_text(data.get("imap_sent_folder")),
            max_per_hour=max_per_hour,
            max_per_day=max_per_day,
        )
        return _apply_guard_settings_and_public(mailbox["id"], data)

    email = _safe_text(data.get("email")).lower()
    token = _safe_text(data.get("api_token")) if transport == "mailopost" else ""
    if not email or "@" not in email:
        raise ValueError("Укажите подтверждённый email отправителя.")
    if transport == "rusender":
        sending_key_id = _optional_sending_key_id(data.get("sending_key_id"))
        _validate_rusender_sending_key_id(sending_key_id)
    else:
        sending_key_id = None
        if not token:
            raise ValueError("Укажите API-токен провайдера.")
    default_base = (
        settings.rusender_api_base_url if transport == "rusender" else settings.mailopost_api_base_url
    )
    api_base_url = _safe_text(data.get("api_base_url") or default_base).rstrip("/")
    if not api_base_url.startswith(("https://", "http://")):
        raise ValueError("Адрес API должен начинаться с https:// или http://.")

    now = _now()
    with session_scope() as session:
        existing = session.execute(
            select(SmtpMailbox).where(SmtpMailbox.owner_username == owner_username).limit(1)
        ).scalar_one_or_none()
        make_default = bool(data.get("make_default")) or existing is None
        if make_default:
            session.execute(
                update(SmtpMailbox)
                .where(SmtpMailbox.owner_username == owner_username, SmtpMailbox.is_default.is_(True))
                .values(is_default=False, updated_at=now)
            )
        row = SmtpMailbox(
            id=str(uuid4()),
            owner_username=owner_username,
            provider=transport,
            email=email,
            sender_name=sender_name,
            host=api_base_url,
            port=443,
            use_ssl=True,
            use_starttls=False,
            auth_method="environment" if transport == "rusender" else "token",
            smtp_username=None,
            password_encrypted="" if transport == "rusender" else encrypt_secret(token),
            status="active",
            sending_key_id=sending_key_id,
            last_error=None,
            is_default=make_default,
            max_per_hour=max_per_hour,
            max_per_day=max_per_day,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        from src.generator.delivery.channel_guard import apply_scoped_guard_settings

        apply_scoped_guard_settings(session, row, data)
        session.flush()
        return _public_connection(row, session)


def update_connection(
    connection_id: str,
    owner_username: str,
    data: dict[str, Any],
    *,
    visible_owners: Any = _CONNECTION_VISIBILITY_UNSET,
) -> dict[str, Any]:
    visible_owners = _effective_connection_visibility(owner_username, visible_owners)
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None or not can_access_owner(visible_owners, row.owner_username):
            raise LookupError("Подключение не найдено.")
        transport = connection_transport(row)
        provider = row.provider
        target_owner = row.owner_username

    requested_transport = _safe_text(data.get("transport") or transport).lower()
    if requested_transport != transport:
        raise ValueError("Тип существующего подключения изменить нельзя. Создайте новое подключение.")

    if transport == "smtp":
        if _safe_text(provider).lower() == "mailru":
            with session_scope() as session:
                row = session.get(SmtpMailbox, connection_id)
                if row is None or not can_access_owner(visible_owners, row.owner_username):
                    raise LookupError("Подключение не найдено.")
                current_email = row.email
                current_password = decrypt_secret(row.password_encrypted)
                current_sender_name = row.sender_name or ""
            email = _normalize_mailru_email(data.get("email") or current_email)
            password = _safe_text(data.get("password")) or current_password
            if "email" in data or _safe_text(data.get("password")):
                _verify_mailru_credentials(
                    email=email,
                    password=password,
                    sender_name=data.get("sender_name") or current_sender_name,
                )
            mailbox = update_mailbox(
                connection_id,
                owner_username=target_owner,
                provider="mailru",
                email=email,
                password=_safe_text(data.get("password")) or None,
                sender_name=data.get("sender_name"),
                host=MAILRU_HOST,
                port=MAILRU_PORT,
                use_ssl=True,
                use_starttls=False,
                smtp_username=email,
                save_sent_copy=data.get("save_sent_copy"),
                imap_host=data.get("imap_host"),
                imap_port=data.get("imap_port"),
                imap_use_ssl=data.get("imap_use_ssl"),
                imap_use_starttls=data.get("imap_use_starttls"),
                imap_username=data.get("imap_username"),
                imap_password=data.get("imap_password"),
                imap_sent_folder=data.get("imap_sent_folder"),
                max_per_hour=_optional_rate_limit(data, "max_per_hour"),
                max_per_day=_optional_rate_limit(data, "max_per_day"),
            )
            return _apply_guard_settings_and_public(mailbox["id"], data)
        mailbox = update_mailbox(
            connection_id,
            owner_username=target_owner,
            provider=provider,
            email=data.get("email"),
            password=_safe_text(data.get("password")) or None,
            sender_name=data.get("sender_name"),
            host=data.get("host"),
            port=data.get("port"),
            use_ssl=data.get("use_ssl"),
            use_starttls=data.get("use_starttls"),
            smtp_username=data.get("smtp_username"),
            save_sent_copy=data.get("save_sent_copy"),
            imap_host=data.get("imap_host"),
            imap_port=data.get("imap_port"),
            imap_use_ssl=data.get("imap_use_ssl"),
            imap_use_starttls=data.get("imap_use_starttls"),
            imap_username=data.get("imap_username"),
            imap_password=data.get("imap_password"),
            imap_sent_folder=data.get("imap_sent_folder"),
            max_per_hour=_optional_rate_limit(data, "max_per_hour"),
            max_per_day=_optional_rate_limit(data, "max_per_day"),
        )
        return _apply_guard_settings_and_public(mailbox["id"], data)

    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None or not can_access_owner(visible_owners, row.owner_username):
            raise LookupError("Подключение не найдено.")
        if "email" in data:
            email = _safe_text(data.get("email")).lower()
            if not email or "@" not in email:
                raise ValueError("Укажите подтверждённый email отправителя.")
            row.email = email
        if "sender_name" in data:
            row.sender_name = _safe_text(data.get("sender_name"))
        if "api_base_url" in data:
            api_base_url = _safe_text(data.get("api_base_url")).rstrip("/")
            if not api_base_url.startswith(("https://", "http://")):
                raise ValueError("Адрес API должен начинаться с https:// или http://.")
            row.host = api_base_url
        if transport == "rusender":
            if "sending_key_id" in data:
                row.sending_key_id = _optional_sending_key_id(data.get("sending_key_id"))
            _validate_rusender_sending_key_id(row.sending_key_id)
        else:
            api_token = _safe_text(data.get("api_token"))
            if api_token:
                row.password_encrypted = encrypt_secret(api_token)
        if "max_per_hour" in data and data.get("max_per_hour") is not None:
            row.max_per_hour = _rate_limit_value(data.get("max_per_hour"))
        if "max_per_day" in data and data.get("max_per_day") is not None:
            row.max_per_day = _rate_limit_value(data.get("max_per_day"))
        from src.generator.delivery.channel_guard import apply_scoped_guard_settings

        apply_scoped_guard_settings(session, row, data)
        if row.delivery_guard_state != "disabled":
            row.status = "active"
            row.last_error = None
        row.updated_at = _now()
        session.flush()
        return _public_connection(row, session)


def delete_connection(
    connection_id: str,
    owner_username: str,
    *,
    visible_owners: Any = _CONNECTION_VISIBILITY_UNSET,
) -> None:
    visible_owners = _effective_connection_visibility(owner_username, visible_owners)
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None or not can_access_owner(visible_owners, row.owner_username):
            raise LookupError("Подключение не найдено.")
        target_owner = row.owner_username
    delete_mailbox(connection_id, owner_username=target_owner)


def reset_connection_guard(
    connection_id: str,
    owner_username: str,
    *,
    visible_owners: Any = _CONNECTION_VISIBILITY_UNSET,
) -> dict[str, Any]:
    visible_owners = _effective_connection_visibility(owner_username, visible_owners)
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None or not can_access_owner(visible_owners, row.owner_username):
            raise LookupError("Delivery connection not found.")
    from src.generator.delivery.channel_guard import reset_channel_guard

    reset_channel_guard(connection_id, enable=True)
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None:
            raise LookupError("Delivery connection not found.")
        return _public_connection(row, session)


def resolve_connection(
    connection_id: str,
    owner_username: str,
    *,
    campaign: Campaign | None = None,
    visible_owners: Any = _CONNECTION_VISIBILITY_UNSET,
) -> ResolvedConnection:
    visible_owners = _effective_connection_visibility(owner_username, visible_owners)
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection_id)
        if row is None or not can_access_owner(visible_owners, row.owner_username):
            raise LookupError("Подключение не найдено.")
        if row.delivery_guard_state == "disabled" or row.status == "disabled_by_guard":
            raise RuntimeError(row.delivery_guard_reason or "Delivery channel is disabled.")
        if channel_state_blocks_send(row.delivery_guard_state, row.status):
            raise RuntimeError(
                "Обычная отправка приостановлена до завершения прогрева подключения."
            )
        transport = connection_transport(row)
        sender_name = resolve_sender_name(
            row.owner_username,
            campaign=campaign,
            fallback=row.sender_name,
        )
        if transport == "smtp":
            return ResolvedConnection(row.id, transport, row.email, sender_name, "", "")
        return ResolvedConnection(
            id=row.id,
            transport=transport,
            email=row.email,
            sender_name=sender_name,
            secret="" if transport == "rusender" else decrypt_secret(row.password_encrypted),
            api_base_url=row.host,
            sending_key_id=row.sending_key_id,
        )


def normalize_connection_ids(raw: list[str] | None, fallback_id: str | None = None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in list(raw or []):
        value = _safe_text(item)
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if not normalized and fallback_id:
        value = _safe_text(fallback_id)
        if value:
            normalized.append(value)
    return normalized


def campaign_connection_ids(campaign: Any) -> list[str]:
    ids = normalize_connection_ids(list(getattr(campaign, "connection_ids", None) or []))
    if ids:
        return ids
    fallback = _safe_text(getattr(campaign, "smtp_mailbox_id", None))
    return [fallback] if fallback else []


def _connection_at_limit(
    row: SmtpMailbox | None,
    *,
    hour_count: int,
    day_count: int,
) -> bool:
    if row is None:
        return True
    max_hour = int(row.max_per_hour or 0)
    max_day = int(row.max_per_day or 0)
    if max_hour > 0 and hour_count >= max_hour:
        return True
    if max_day > 0 and day_count >= max_day:
        return True
    return False


def pick_available_connection(
    ids: list[str],
    owner_username: str,
    hour_counts: dict[str, int],
    day_counts: dict[str, int],
    *,
    campaign: Campaign | None = None,
) -> ResolvedConnection | None:
    if not ids:
        return None
    with session_scope() as session:
        for connection_id in ids:
            row = session.get(SmtpMailbox, connection_id)
            if row is None or row.owner_username != owner_username:
                continue
            if channel_state_blocks_send(row.delivery_guard_state, row.status):
                continue
            if _connection_at_limit(
                row,
                hour_count=int(hour_counts.get(connection_id, 0)),
                day_count=int(day_counts.get(connection_id, 0)),
            ):
                continue
            transport = connection_transport(row)
            sender_name = resolve_sender_name(
                owner_username,
                campaign=campaign,
                fallback=row.sender_name,
            )
            if transport == "smtp":
                return ResolvedConnection(row.id, transport, row.email, sender_name, "", "")
            return ResolvedConnection(
                id=row.id,
                transport=transport,
                email=row.email,
                sender_name=sender_name,
                secret="" if transport == "rusender" else decrypt_secret(row.password_encrypted),
                api_base_url=row.host,
                sending_key_id=row.sending_key_id,
            )
    return None


def validate_connection_ids(ids: list[str], owner_username: str) -> str | None:
    normalized = normalize_connection_ids(ids)
    if not normalized:
        return "Выберите подключение отправителя"
    for connection_id in normalized:
        try:
            resolve_connection(connection_id, owner_username)
        except (LookupError, RuntimeError):
            return "Выбранное подключение не найдено"
    return None


def validate_connection_choice(connection_id: str | None, owner_username: str, transport: str) -> str | None:
    if not connection_id:
        return "Выберите подключение отправителя"
    try:
        connection = resolve_connection(connection_id, owner_username)
    except (LookupError, RuntimeError):
        return "Выбранное подключение не найдено"
    normalized = _safe_text(transport or connection.transport).lower()
    if normalized != connection.transport:
        return "Тип подключения не совпадает с выбранным способом отправки"
    return None


def test_connection(
    connection_id: str,
    owner_username: str,
    *,
    visible_owners: Any = _CONNECTION_VISIBILITY_UNSET,
) -> dict[str, Any]:
    connection = resolve_connection(connection_id, owner_username, visible_owners=visible_owners)
    with session_scope() as session:
        row = session.get(SmtpMailbox, connection.id)
        if row is None:
            raise LookupError("Delivery connection not found.")
        target_owner = row.owner_username
    credentials: ResolvedSmtpCredentials | None = None
    try:
        if connection.transport == "smtp":
            credentials = resolve_smtp_credentials(mailbox_id=connection.id, owner_username=target_owner)
            verify_and_mark_mailbox(credentials, mailbox_id=connection.id, send_test=False)
            message = "SMTP-подключение успешно проверено."
            try:
                from src.generator.delivery.imap_sent import verify_imap_connection

                imap_result = verify_imap_connection(
                    mailbox_id=connection.id,
                    owner_username=target_owner,
                )
                if imap_result.get("status") == "ok":
                    message += f" Копии будут сохраняться в папку «{imap_result.get('folder') or 'Отправленные'}»."
            except Exception as imap_exc:
                return {
                    "status": "warning",
                    "message": (
                        f"{message} Но сохранение копий через IMAP не настроено: {imap_exc}"
                    ),
                }
        else:
            from src.campaigns.batch_worker import _send_delivery_message
            from src.generator.delivery.email_validation import validate_email_address

            email_validation = validate_email_address(connection.email, mode="syntax")
            if not email_validation.is_valid:
                raise ValueError(email_validation.reason or "Некорректный email подключения.")

            _send_delivery_message(
                connection_id=connection.id,
                owner_username=target_owner,
                to_email=email_validation.normalized_email,
                subject="Проверка подключения ai-offer",
                html="<p>Подключение успешно. Это тестовое письмо ai-offer.</p>",
                text="Подключение успешно. Это тестовое письмо ai-offer.",
            )
            message = f"{connection.transport} проверен: тестовое письмо отправлено на {connection.email}."
        mark_mailbox_status(connection.id, status="active", last_error="")
        return {"status": "ok", "message": message}
    except Exception as exc:
        if connection.transport == "smtp":
            error_message = humanize_smtp_error(
                exc,
                provider=connection.provider,
                host=credentials.host if credentials else connection.host,
                email=credentials.email if credentials else connection.email,
            )
        else:
            error_message = str(exc)
        mark_mailbox_status(connection.id, status="auth_failed", last_error=error_message)
        if connection.transport == "smtp":
            raise ValueError(error_message) from exc
        raise
