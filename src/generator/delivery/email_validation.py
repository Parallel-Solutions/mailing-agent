from __future__ import annotations

import re
import socket
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any


EMAIL_VALIDATION_OFF = "off"
EMAIL_VALIDATION_SYNTAX = "syntax"
EMAIL_VALIDATION_DOMAIN = "domain"
EMAIL_VALIDATION_MODES = {
    EMAIL_VALIDATION_OFF,
    EMAIL_VALIDATION_SYNTAX,
    EMAIL_VALIDATION_DOMAIN,
}

_LOCAL_PART_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_DOMAIN_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True)
class EmailValidationResult:
    email: str
    normalized_email: str
    domain: str
    is_valid: bool
    reason_code: str
    reason: str
    checked_at: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_email_validation_mode(value: Any) -> str:
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in {"", "true", "1", "yes", "on", "domain_dns", "dns"}:
        return EMAIL_VALIDATION_DOMAIN
    if mode in {"false", "0", "no", "none", "disabled"}:
        return EMAIL_VALIDATION_OFF
    if mode in {"format", "basic"}:
        return EMAIL_VALIDATION_SYNTAX
    if mode in EMAIL_VALIDATION_MODES:
        return mode
    return EMAIL_VALIDATION_DOMAIN


def validate_email_address(
    email: Any,
    *,
    mode: str = EMAIL_VALIDATION_DOMAIN,
    timeout_seconds: float = 3.0,
) -> EmailValidationResult:
    raw_email = str(email or "").strip()
    checked_at = datetime.now().isoformat(timespec="seconds")
    normalized_mode = normalize_email_validation_mode(mode)

    if normalized_mode == EMAIL_VALIDATION_OFF:
        return EmailValidationResult(
            email=raw_email,
            normalized_email=raw_email,
            domain="",
            is_valid=bool(raw_email),
            reason_code="validation_disabled" if raw_email else "empty",
            reason="" if raw_email else "Email пустой.",
            checked_at=checked_at,
            details={"mode": normalized_mode},
        )

    syntax_result = _validate_email_syntax(raw_email, checked_at=checked_at, mode=normalized_mode)
    if not syntax_result.is_valid or normalized_mode == EMAIL_VALIDATION_SYNTAX:
        return syntax_result

    has_route, reason_code, reason, details = _domain_has_mail_route(
        syntax_result.domain,
        timeout_seconds=timeout_seconds,
    )
    if not has_route:
        return EmailValidationResult(
            email=raw_email,
            normalized_email=syntax_result.normalized_email,
            domain=syntax_result.domain,
            is_valid=False,
            reason_code=reason_code,
            reason=reason,
            checked_at=checked_at,
            details={**syntax_result.details, **details, "mode": normalized_mode},
        )

    return EmailValidationResult(
        email=raw_email,
        normalized_email=syntax_result.normalized_email,
        domain=syntax_result.domain,
        is_valid=True,
        reason_code=reason_code,
        reason="",
        checked_at=checked_at,
        details={**syntax_result.details, **details, "mode": normalized_mode},
    )


def _validate_email_syntax(email: str, *, checked_at: str, mode: str) -> EmailValidationResult:
    def invalid(reason_code: str, reason: str, details: dict[str, Any] | None = None) -> EmailValidationResult:
        return EmailValidationResult(
            email=email,
            normalized_email=email,
            domain="",
            is_valid=False,
            reason_code=reason_code,
            reason=reason,
            checked_at=checked_at,
            details={"mode": mode, **(details or {})},
        )

    if not email:
        return invalid("empty", "Email пустой.")
    if len(email) > 254:
        return invalid("too_long", "Email слишком длинный.")
    if email.count("@") != 1:
        return invalid("invalid_format", "Email должен содержать один символ @.")

    local_part, domain = email.rsplit("@", 1)
    local_part = local_part.strip()
    domain = domain.strip().strip(".")
    if not local_part or not domain:
        return invalid("invalid_format", "Email должен содержать имя ящика и домен.")
    if len(local_part.encode("utf-8")) > 64:
        return invalid("local_too_long", "Имя ящика до @ слишком длинное.")
    if local_part.startswith(".") or local_part.endswith(".") or ".." in local_part:
        return invalid("invalid_local_part", "В имени ящика некорректно расставлены точки.")
    if not _LOCAL_PART_PATTERN.match(local_part):
        return invalid("invalid_local_part", "В имени ящика есть недопустимые символы.")

    try:
        ascii_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return invalid("invalid_domain", "Домен email записан некорректно.")
    if "." not in ascii_domain:
        return invalid("invalid_domain", "В домене email нет точки.")
    if len(ascii_domain) > 253:
        return invalid("domain_too_long", "Домен email слишком длинный.")

    labels = ascii_domain.split(".")
    for label in labels:
        if not label:
            return invalid("invalid_domain", "В домене email есть пустая часть.")
        if len(label) > 63:
            return invalid("invalid_domain", "Часть домена email слишком длинная.")
        if label.startswith("-") or label.endswith("-") or not _DOMAIN_LABEL_PATTERN.match(label):
            return invalid("invalid_domain", "Домен email записан некорректно.")

    normalized_email = f"{local_part}@{ascii_domain}"
    return EmailValidationResult(
        email=email,
        normalized_email=normalized_email,
        domain=ascii_domain,
        is_valid=True,
        reason_code="ok_syntax",
        reason="",
        checked_at=checked_at,
        details={"mode": mode},
    )


def _domain_has_mail_route(domain: str, *, timeout_seconds: float) -> tuple[bool, str, str, dict[str, Any]]:
    timeout_key = max(1, min(30, int(round(float(timeout_seconds or 3.0)))))
    return _cached_domain_has_mail_route(domain, timeout_key)


@lru_cache(maxsize=4096)
def _cached_domain_has_mail_route(domain: str, timeout_seconds: int) -> tuple[bool, str, str, dict[str, Any]]:
    dns_result = _domain_has_mx_record(domain, timeout_seconds=timeout_seconds)
    if dns_result is not None:
        has_mx, reason_code, reason, details = dns_result
        if has_mx or reason_code == "domain_not_found":
            return has_mx, reason_code, reason, details

    try:
        socket.getaddrinfo(domain, None)
    except socket.gaierror as exc:
        return (
            False,
            "domain_not_found",
            f"Email не прошёл проверку: домен {domain} не найден.",
            {"dns_error": str(exc), "domain_check": "address"},
        )
    except OSError as exc:
        return (
            False,
            "domain_lookup_failed",
            f"Email не прошёл проверку: не удалось проверить домен {domain}.",
            {"dns_error": str(exc), "domain_check": "address"},
        )

    return True, "ok_domain", "", {"domain_check": "address"}


def _domain_has_mx_record(domain: str, *, timeout_seconds: int) -> tuple[bool, str, str, dict[str, Any]] | None:
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return None

    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout_seconds
    resolver.lifetime = timeout_seconds
    try:
        answers = resolver.resolve(domain, "MX")
    except dns.resolver.NXDOMAIN as exc:
        return (
            False,
            "domain_not_found",
            f"Email не прошёл проверку: домен {domain} не найден.",
            {"dns_error": str(exc), "domain_check": "mx"},
        )
    except dns.resolver.NoAnswer:
        return False, "no_mx_record", "", {"domain_check": "mx"}
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        return (
            False,
            "domain_lookup_failed",
            f"Email не прошёл проверку: не удалось проверить домен {domain}.",
            {"dns_error": str(exc), "domain_check": "mx"},
        )

    if list(answers):
        return True, "ok_mx", "", {"domain_check": "mx"}
    return False, "no_mx_record", "", {"domain_check": "mx"}
