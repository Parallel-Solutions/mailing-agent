from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.generator.delivery.smtp_mailboxes import (
    ResolvedSmtpCredentials,
    build_oauth_credentials,
    humanize_smtp_error,
    normalize_smtp_secret,
    verify_smtp_credentials,
)
from src.generator.delivery.smtp_oauth import (
    OAuthTokens,
    is_oauth_provider_configured,
    oauth_provider_for_email,
)
from src.generator.delivery.smtp_probe import ProbeResult, probe_smtp_for_email
from src.generator.delivery.smtp_providers import resolve_provider_settings
from src.generator.delivery.smtp_setup_ai import SetupAction, advise_smtp_setup, build_fallback_setup_action
from src.generator.delivery.smtp_autodiscover import parse_discover_email, result_to_dict


_SETUP_SESSION_TTL_SECONDS = 15 * 60
_PROBE_BUDGET_SECONDS = 12.0
_setup_sessions: dict[str, dict[str, Any]] = {}
logger = logging.getLogger(__name__)


@dataclass
class SetupAnalysis:
    setup_session_id: str
    email: str
    domain: str
    probe: ProbeResult | None
    discoveries: list[dict[str, Any]]
    action: SetupAction
    oauth_available: dict[str, bool] = field(default_factory=dict)
    probe_status: str = "skipped"
    discovery_applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_session_id": self.setup_session_id,
            "email": self.email,
            "domain": self.domain,
            "probe": self.probe.to_dict() if self.probe else None,
            "discoveries": self.discoveries,
            "action": self.action.to_dict(),
            "oauth_available": self.oauth_available,
            "probe_status": self.probe_status,
            "discovery_applied": self.discovery_applied,
        }


def analyze_smtp_setup(
    email: str,
    *,
    last_error: str = "",
    attempt_count: int = 0,
    setup_session_id: str | None = None,
) -> SetupAnalysis:
    _cleanup_expired_sessions()
    started_at = time.monotonic()
    normalized_email, domain = parse_discover_email(email)
    probe, discoveries = probe_smtp_for_email(
        normalized_email,
        deadline=started_at + _PROBE_BUDGET_SECONDS,
    )
    probe_elapsed = time.monotonic() - started_at
    discovery_payload = [
        result_to_dict(item, email=normalized_email, domain=domain) for item in discoveries
    ]
    provider_hint = _resolve_provider_hint(probe, discoveries)
    probe_status = _resolve_probe_status(probe, discoveries)
    discovery_applied = bool(discovery_payload)
    oauth_available = {
        "google": is_oauth_provider_configured("google"),
        "microsoft": is_oauth_provider_configured("microsoft"),
    }
    context = {
        "email": normalized_email,
        "domain": domain,
        "provider_hint": provider_hint,
        "probe": probe.to_dict() if probe else None,
        "discoveries": discovery_payload,
        "last_error": last_error,
        "attempt_count": attempt_count,
        "oauth_available": oauth_available,
    }
    action = advise_smtp_setup(context)
    ai_elapsed = time.monotonic() - started_at - probe_elapsed
    if action.action in {"show_manual", "retry_probe", "contact_admin"} and (
        (probe and probe.reachable) or _has_high_confidence_discovery(discoveries)
    ):
        action = build_fallback_setup_action(context)
    session_id = setup_session_id or str(uuid4())
    _setup_sessions[session_id] = {
        "expires_at": time.time() + _SETUP_SESSION_TTL_SECONDS,
        "email": normalized_email,
        "domain": domain,
        "probe": probe.to_dict() if probe else None,
        "discoveries": discovery_payload,
        "provider_hint": provider_hint,
    }
    total_elapsed = time.monotonic() - started_at
    logger.info(
        "smtp_setup_analyze email=%s domain=%s probe_reachable=%s probe_seconds=%.2f ai_seconds=%.2f total_seconds=%.2f action=%s",
        normalized_email,
        domain,
        bool(probe and probe.reachable),
        probe_elapsed,
        ai_elapsed,
        total_elapsed,
        action.action,
    )
    return SetupAnalysis(
        setup_session_id=session_id,
        email=normalized_email,
        domain=domain,
        probe=probe,
        discoveries=discovery_payload,
        action=action,
        oauth_available=oauth_available,
        probe_status=probe_status,
        discovery_applied=discovery_applied,
    )


def verify_smtp_setup(
    *,
    setup_session_id: str,
    email: str,
    password: str = "",
    oauth_provider: str | None = None,
    oauth_tokens: OAuthTokens | None = None,
    provider: str = "custom",
    host: str = "",
    port: int | None = None,
    use_ssl: bool | None = None,
    use_starttls: bool | None = None,
    smtp_username: str | None = None,
) -> dict[str, Any]:
    _cleanup_expired_sessions()
    session = _setup_sessions.get(setup_session_id)
    normalized_email, domain = parse_discover_email(email)
    if session and session.get("email") != normalized_email:
        raise ValueError("Email не совпадает с сессией настройки.")

    probe_data = (session or {}).get("probe") or {}
    preset = resolve_provider_settings(
        provider or probe_data.get("provider") or "custom",
        host=host or probe_data.get("host") or "",
        port=port if port is not None else probe_data.get("port"),
        use_ssl=use_ssl if use_ssl is not None else probe_data.get("use_ssl"),
        use_starttls=use_starttls if use_starttls is not None else probe_data.get("use_starttls"),
    )

    auth_method = "password"
    safe_password = normalize_smtp_secret(password)
    safe_oauth_provider = str(oauth_provider or "").strip().lower() or None
    if oauth_tokens is not None and safe_oauth_provider:
        auth_method = "oauth"
        credentials = build_oauth_credentials(
            email=normalized_email,
            provider=safe_oauth_provider,
            tokens=oauth_tokens,
            host=preset.host,
            port=preset.port,
            use_ssl=preset.use_ssl,
            use_starttls=preset.use_starttls,
            sender_name="",
            smtp_username=smtp_username,
        )
    else:
        if not safe_password:
            raise ValueError("Укажите пароль или выполните OAuth-вход.")
        credentials = ResolvedSmtpCredentials(
            email=normalized_email,
            password=safe_password,
            host=preset.host,
            port=preset.port,
            use_ssl=preset.use_ssl,
            use_starttls=preset.use_starttls,
            sender_name="",
            auth_method="password",
            smtp_username=str(smtp_username or normalized_email),
        )

    try:
        verify_smtp_credentials(credentials)
    except Exception as exc:
        analysis = analyze_smtp_setup(
            normalized_email,
            last_error=humanize_smtp_error(exc),
            attempt_count=int((session or {}).get("attempt_count", 0)) + 1,
            setup_session_id=setup_session_id,
        )
        return {
            "verified": False,
            "error": humanize_smtp_error(exc),
            "analysis": analysis.to_dict(),
            "settings": {
                "provider": preset.id,
                "host": preset.host,
                "port": preset.port,
                "use_ssl": preset.use_ssl,
                "use_starttls": preset.use_starttls,
                "auth_method": auth_method,
                "oauth_provider": safe_oauth_provider,
            },
        }

    return {
        "verified": True,
        "settings": {
            "provider": preset.id,
            "host": preset.host,
            "port": preset.port,
            "use_ssl": preset.use_ssl,
            "use_starttls": preset.use_starttls,
            "auth_method": auth_method,
            "oauth_provider": safe_oauth_provider,
        },
        "oauth_tokens": oauth_tokens.to_dict() if oauth_tokens else None,
    }


def create_oauth_state(*, setup_session_id: str, provider: str, email: str) -> str:
    state = secrets.token_urlsafe(24)
    _setup_sessions[f"oauth:{state}"] = {
        "expires_at": time.time() + _SETUP_SESSION_TTL_SECONDS,
        "setup_session_id": setup_session_id,
        "provider": provider,
        "email": email,
    }
    return state


def pop_oauth_state(state: str) -> dict[str, Any] | None:
    _cleanup_expired_sessions()
    key = f"oauth:{state}"
    payload = _setup_sessions.pop(key, None)
    return payload if isinstance(payload, dict) else None


def recommended_oauth_provider(email: str) -> str | None:
    normalized_email, _domain = parse_discover_email(email)
    del normalized_email
    return oauth_provider_for_email(email)


def _resolve_provider_hint(
    probe: ProbeResult | None,
    discoveries: list[Any],
) -> str:
    if probe and probe.reachable and probe.provider:
        return probe.provider
    if discoveries:
        provider = str(discoveries[0].provider or "").strip().lower()
        if provider and provider != "custom":
            return provider
    if probe and probe.provider:
        return probe.provider
    return "custom"


def _resolve_probe_status(
    probe: ProbeResult | None,
    discoveries: list[Any],
) -> str:
    if probe is None:
        if _has_high_confidence_discovery(discoveries):
            return "skipped"
        return "unreachable"
    if probe.reachable:
        return "reachable"
    if _has_high_confidence_discovery(discoveries):
        return "skipped"
    return "unreachable"


def _has_high_confidence_discovery(discoveries: list[Any]) -> bool:
    return bool(discoveries) and str(discoveries[0].confidence or "").strip().lower() == "high"


def _cleanup_expired_sessions() -> None:
    now = time.time()
    expired = [key for key, value in _setup_sessions.items() if float(value.get("expires_at") or 0) < now]
    for key in expired:
        _setup_sessions.pop(key, None)
