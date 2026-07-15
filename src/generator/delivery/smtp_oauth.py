from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.generator.delivery.smtp_autodiscover import DOMAIN_PRESET_MAP, parse_discover_email
from src.security.credential_vault import decrypt_secret, encrypt_secret
from src.utils.config import settings
from src.utils.env import resolve_env_value

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_MICROSOFT_AUTH_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
_MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_in: int = 0
    scope: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OAuthTokens":
        return cls(
            access_token=str(payload.get("access_token") or ""),
            refresh_token=str(payload.get("refresh_token") or ""),
            token_type=str(payload.get("token_type") or "Bearer"),
            expires_in=int(payload.get("expires_in") or 0),
            scope=str(payload.get("scope") or ""),
        )


def is_oauth_provider_configured(provider: str) -> bool:
    normalized = str(provider or "").strip().lower()
    if normalized == "google":
        return bool(_google_client_id() and _google_client_secret())
    if normalized == "microsoft":
        return bool(_microsoft_client_id() and _microsoft_client_secret())
    return False


def oauth_provider_for_email(email: str) -> str | None:
    _, domain = parse_discover_email(email)
    provider = DOMAIN_PRESET_MAP.get(domain)
    if provider == "gmail":
        return "google"
    if provider == "outlook":
        return "microsoft"
    return None


def build_oauth_authorize_url(*, provider: str, state: str, email: str) -> str:
    normalized = str(provider or "").strip().lower()
    redirect_uri = _oauth_redirect_uri(normalized)
    if normalized == "google":
        params = {
            "client_id": _google_client_id(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "https://mail.google.com/",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "login_hint": email,
        }
        return f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    if normalized == "microsoft":
        params = {
            "client_id": _microsoft_client_id(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "https://outlook.office.com/SMTP.Send offline_access openid profile email",
            "state": state,
            "login_hint": email,
        }
        tenant = _microsoft_tenant()
        return f"{_MICROSOFT_AUTH_URL.format(tenant=tenant)}?{urllib.parse.urlencode(params)}"
    raise ValueError("Неподдерживаемый OAuth-провайдер.")


def exchange_oauth_code(*, provider: str, code: str) -> OAuthTokens:
    normalized = str(provider or "").strip().lower()
    redirect_uri = _oauth_redirect_uri(normalized)
    if normalized == "google":
        payload = _post_form(
            _GOOGLE_TOKEN_URL,
            {
                "client_id": _google_client_id(),
                "client_secret": _google_client_secret(),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        return OAuthTokens.from_dict(payload)
    if normalized == "microsoft":
        tenant = _microsoft_tenant()
        payload = _post_form(
            _MICROSOFT_TOKEN_URL.format(tenant=tenant),
            {
                "client_id": _microsoft_client_id(),
                "client_secret": _microsoft_client_secret(),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        return OAuthTokens.from_dict(payload)
    raise ValueError("Неподдерживаемый OAuth-провайдер.")


def refresh_oauth_tokens(*, provider: str, refresh_token: str) -> OAuthTokens:
    normalized = str(provider or "").strip().lower()
    if normalized == "google":
        payload = _post_form(
            _GOOGLE_TOKEN_URL,
            {
                "client_id": _google_client_id(),
                "client_secret": _google_client_secret(),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        tokens = OAuthTokens.from_dict(payload)
        if not tokens.refresh_token:
            return OAuthTokens(
                access_token=tokens.access_token,
                refresh_token=refresh_token,
                token_type=tokens.token_type,
                expires_in=tokens.expires_in,
                scope=tokens.scope,
            )
        return tokens
    if normalized == "microsoft":
        tenant = _microsoft_tenant()
        payload = _post_form(
            _MICROSOFT_TOKEN_URL.format(tenant=tenant),
            {
                "client_id": _microsoft_client_id(),
                "client_secret": _microsoft_client_secret(),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": "https://outlook.office.com/SMTP.Send offline_access openid profile email",
            },
        )
        tokens = OAuthTokens.from_dict(payload)
        if not tokens.refresh_token:
            return OAuthTokens(
                access_token=tokens.access_token,
                refresh_token=refresh_token,
                token_type=tokens.token_type,
                expires_in=tokens.expires_in,
                scope=tokens.scope,
            )
        return tokens
    raise ValueError("Неподдерживаемый OAuth-провайдер.")


def encrypt_oauth_tokens(tokens: OAuthTokens) -> str:
    return encrypt_secret(json.dumps(tokens.to_dict(), ensure_ascii=False))


def decrypt_oauth_tokens(value: str) -> OAuthTokens:
    payload = json.loads(decrypt_secret(value))
    if not isinstance(payload, dict):
        raise ValueError("Некорректный OAuth-токен.")
    return OAuthTokens.from_dict(payload)


def build_xoauth2_string(email: str, access_token: str) -> str:
    import base64

    auth_string = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(auth_string.encode("utf-8")).decode("ascii")


def _oauth_redirect_uri(provider: str) -> str:
    base = str(resolve_env_value("SMTP_OAUTH_REDIRECT_BASE") or settings.smtp_oauth_redirect_base or "").strip()
    if not base:
        base = "http://localhost:9806"
    return f"{base.rstrip('/')}/api/smtp/oauth/callback/{provider}"


def _google_client_id() -> str:
    return str(resolve_env_value("GOOGLE_OAUTH_CLIENT_ID") or settings.google_oauth_client_id or "").strip()


def _google_client_secret() -> str:
    return str(resolve_env_value("GOOGLE_OAUTH_CLIENT_SECRET") or settings.google_oauth_client_secret or "").strip()


def _microsoft_client_id() -> str:
    return str(resolve_env_value("MICROSOFT_OAUTH_CLIENT_ID") or settings.microsoft_oauth_client_id or "").strip()


def _microsoft_client_secret() -> str:
    return str(
        resolve_env_value("MICROSOFT_OAUTH_CLIENT_SECRET") or settings.microsoft_oauth_client_secret or ""
    ).strip()


def _microsoft_tenant() -> str:
    return str(resolve_env_value("MICROSOFT_OAUTH_TENANT") or settings.microsoft_oauth_tenant or "common").strip()


def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("OAuth provider returned invalid payload.")
    if data.get("error"):
        description = str(data.get("error_description") or data.get("error") or "OAuth error")
        raise ValueError(description)
    return data
