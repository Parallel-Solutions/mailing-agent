from __future__ import annotations

import json
import re
import socket
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.infra.spend_ledger import record_service_call


EMAIL_VALIDATION_OFF = "off"
EMAIL_VALIDATION_SYNTAX = "syntax"
EMAIL_VALIDATION_DOMAIN = "domain"
EMAIL_VALIDATION_SMTPBZ = "smtpbz"
EMAIL_VALIDATION_MODES = {
    EMAIL_VALIDATION_OFF,
    EMAIL_VALIDATION_SYNTAX,
    EMAIL_VALIDATION_DOMAIN,
    EMAIL_VALIDATION_SMTPBZ,
}

_LOCAL_PART_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_DOMAIN_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
_SMTPBZ_INVALID_TEXT_RE = re.compile(
    r"(doesn'?t\s+exist|not\s+exist|not\s+found|no\s+such\s+user|user\s+unknown|"
    r"invalid\s+recipient|undeliverable|mailbox\s+unavailable|mailbox\s+not|"
    r"recipient\s+rejected|recipient\s+invalid|adres.*ne.*sushchestv|yashchik.*ne.*sushchestv)",
    re.I,
)
_SMTPBZ_VALID_TEXT_RE = re.compile(r"^(valid|ok|success|deliverable|exists|true|yes)$", re.I)
_SMTPBZ_VALID_KEYS = {
    "valid",
    "is_valid",
    "exists",
    "is_exists",
    "deliverable",
    "is_deliverable",
    "email_valid",
    "address_valid",
}
_SMTPBZ_REQUIRED_CHECK_KEYS = {
    "validsyntax",
    "valid_syntax",
    "validmxrecord",
    "valid_mx_record",
    "validdeliver",
    "valid_deliver",
    "valid_delivery",
}


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
    if not mode:
        return EMAIL_VALIDATION_DOMAIN
    if mode in {"true", "1", "yes", "on", "domain_dns", "dns"}:
        return EMAIL_VALIDATION_DOMAIN
    if mode in {"false", "0", "no", "none", "disabled"}:
        return EMAIL_VALIDATION_OFF
    if mode in {"format", "basic"}:
        return EMAIL_VALIDATION_SYNTAX
    if mode in {"smtp_bz", "smtp.bz", "smtpbz_api", "smtp_delivery", "external_smtp"}:
        return EMAIL_VALIDATION_SMTPBZ
    if mode in EMAIL_VALIDATION_MODES:
        return mode
    return EMAIL_VALIDATION_DOMAIN


def validate_configured_email_address(email: Any, *, config: Any = None) -> EmailValidationResult:
    """Validate an outgoing address using the application's delivery settings."""
    if config is None:
        from src.utils.config import settings

        config = settings
    try:
        timeout_seconds = max(
            1.0,
            float(getattr(config, "email_validation_timeout_seconds", 10.0) or 10.0),
        )
    except (TypeError, ValueError):
        timeout_seconds = 10.0
    return validate_email_address(
        email,
        mode=getattr(config, "email_validation_mode", EMAIL_VALIDATION_DOMAIN),
        timeout_seconds=timeout_seconds,
        smtpbz_api_key=getattr(config, "smtpbz_api_key", ""),
        smtpbz_api_base_url=getattr(config, "smtpbz_api_base_url", ""),
        smtpbz_fail_open=bool(getattr(config, "smtpbz_fail_open", False)),
    )


def validate_email_address(
    email: Any,
    *,
    mode: str = EMAIL_VALIDATION_DOMAIN,
    timeout_seconds: float = 3.0,
    smtpbz_api_key: str | None = None,
    smtpbz_api_base_url: str | None = None,
    smtpbz_fail_open: bool = False,
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

    if normalized_mode == EMAIL_VALIDATION_SMTPBZ:
        smtpbz_valid, smtpbz_reason_code, smtpbz_reason, smtpbz_details = _validate_email_with_smtpbz(
            syntax_result.normalized_email,
            api_key=smtpbz_api_key,
            base_url=smtpbz_api_base_url,
            timeout_seconds=timeout_seconds,
            fail_open=smtpbz_fail_open,
        )
        return EmailValidationResult(
            email=raw_email,
            normalized_email=syntax_result.normalized_email,
            domain=syntax_result.domain,
            # Inconclusive provider failures are fail-open inside
            # ``_smtpbz_check_failure``. Only an explicit invalid mailbox
            # classification blocks this concrete send target.
            is_valid=smtpbz_valid,
            reason_code=smtpbz_reason_code,
            reason=smtpbz_reason,
            checked_at=checked_at,
            details={
                **syntax_result.details,
                **details,
                **smtpbz_details,
                "mode": normalized_mode,
            },
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


def _validate_email_with_smtpbz(
    email: str,
    *,
    api_key: str | None,
    base_url: str | None,
    timeout_seconds: float,
    fail_open: bool,
) -> tuple[bool, str, str, dict[str, Any]]:
    api_key = str(api_key or "").strip()
    if not api_key:
        return _smtpbz_check_failure(
            "smtpbz_not_configured",
            "SMTP.BZ не настроен: адрес проверен только по синтаксису и DNS.",
            fail_open=fail_open,
            details={"status": "skipped", "reason": "api_key_missing"},
        )

    request = Request(
        _build_smtpbz_check_url(email, base_url=base_url),
        method="GET",
        headers={"Accept": "application/json", "Authorization": api_key},
    )
    try:
        raw = _run_smtpbz_request(request, timeout=max(1.0, float(timeout_seconds or 3.0)))
    except HTTPError as exc:
        # SMTP.BZ answered (even with an HTTP error status), so the lookup
        # attempt was billed regardless of the outcome.
        record_service_call(service="smtp_bz", operation="validate_email", status="error")
        raw = getattr(exc, "raw_body", "")
        data = _loads_json_object(raw)
        classification = _classify_smtpbz_response(data if data is not None else raw)
        if classification is not None:
            is_valid, reason_code, reason, details = classification
            details["smtpbz"] = {**details.get("smtpbz", {}), "http_status": exc.code}
            return is_valid, reason_code, reason, details
        if exc.code == 401:
            return _smtpbz_check_failure(
                "smtpbz_unauthorized",
                "SMTP.BZ отклонил API-ключ; результат внешней проверки не учтён.",
                fail_open=fail_open,
                details={"status": "error", "http_status": exc.code},
            )
        if exc.code == 400:
            return _smtpbz_check_failure(
                "smtpbz_quota_or_request_error",
                "SMTP.BZ не выполнил проверку: проверьте квоту валидатора.",
                fail_open=fail_open,
                details={"status": "error", "http_status": exc.code},
            )
        return _smtpbz_check_failure(
            "smtpbz_unavailable",
            "SMTP.BZ временно недоступен; адрес проверен только по синтаксису и DNS.",
            fail_open=fail_open,
            details={"status": "error", "http_status": exc.code, "error": str(exc)},
        )
    except (URLError, OSError, TimeoutError) as exc:
        return _smtpbz_check_failure(
            "smtpbz_unavailable",
            "SMTP.BZ временно недоступен; адрес проверен только по синтаксису и DNS.",
            fail_open=fail_open,
            details={"status": "error", "error": str(exc)},
        )

    record_service_call(service="smtp_bz", operation="validate_email")
    data = _loads_json_object(raw)
    classification = _classify_smtpbz_response(data if data is not None else raw)
    if classification is None:
        return _smtpbz_check_failure(
            "smtpbz_unknown",
            "SMTP.BZ не подтвердил доставляемость; результат оставлен справочным.",
            fail_open=fail_open,
            details={"status": "unknown", "raw": str(raw)[:500]},
        )
    return classification


def _smtpbz_check_failure(
    reason_code: str,
    reason: str,
    *,
    fail_open: bool,
    details: dict[str, Any],
) -> tuple[bool, str, str, dict[str, Any]]:
    # SMTP mailbox probing is advisory: receiving servers may hide mailbox
    # existence, greylist a probe, or accept a recipient before rejecting the
    # message later.  An unavailable or inconclusive third-party check must
    # therefore never turn a syntactically and DNS-valid address into a hard
    # failure.  ``fail_open`` is retained in details for configuration
    # compatibility, but delivery is always allowed for these unknown states.
    return (
        True,
        reason_code,
        reason,
        {
            "smtpbz": {
                **details,
                "advisory": True,
                "configured_fail_open": bool(fail_open),
            }
        },
    )


def _build_smtpbz_check_url(email: str, *, base_url: str | None) -> str:
    root = str(base_url or "https://api.smtp.bz/v1").strip().rstrip("/") or "https://api.smtp.bz/v1"
    return f"{root}/check/email/{quote(email, safe='')}"


def _run_smtpbz_request(request: Request, *, timeout: float) -> str:
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        try:
            exc.raw_body = exc.read().decode("utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            exc.raw_body = ""  # type: ignore[attr-defined]
        raise


def _loads_json_object(raw: Any) -> dict[str, Any] | list[Any] | None:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw or ""))
    except (TypeError, json.JSONDecodeError):
        return None


def _classify_smtpbz_response(payload: Any) -> tuple[bool, str, str, dict[str, Any]] | None:
    values = list(_flatten_smtpbz_values(payload))
    details = {"smtpbz": {"status": "checked", "response": _safe_response_preview(payload)}}
    delivery_keys = {"validdeliver", "valid_deliver", "valid_delivery"}
    delivery_confirmed = False

    text_values = [str(value or "").strip() for _, value in values if value not in (None, "", [], {})]
    joined_text = " ".join(text_values)
    if _SMTPBZ_INVALID_TEXT_RE.search(joined_text):
        return False, "smtpbz_invalid", "SMTP.BZ: email не существует или некорректен.", details

    # A failed SMTP delivery probe is not proof that the mailbox is absent.
    # Greylisting, anti-enumeration policies, catch-all domains and protocol
    # errors all make this check inconclusive. Only positive delivery evidence
    # is promoted to ``valid``; negative probe results remain ``unknown``.
    for key, value in values:
        key_l = key.lower().rsplit(".", 1)[-1]
        if key_l not in _SMTPBZ_REQUIRED_CHECK_KEYS:
            continue
        if key_l in delivery_keys and (
            value is True
            or value == 1
            or (isinstance(value, str) and _SMTPBZ_VALID_TEXT_RE.match(value.strip()))
        ):
            delivery_confirmed = True

    for key, value in values:
        key_l = key.lower().rsplit(".", 1)[-1]
        if key_l in _SMTPBZ_VALID_KEYS and isinstance(value, bool):
            if value:
                if delivery_confirmed:
                    return True, "ok_smtpbz", "", details
                continue
            if key_l in {"exists", "is_exists", "email_valid", "address_valid"}:
                return False, "smtpbz_invalid", "SMTP.BZ: email не существует или некорректен.", details

    for key, value in values:
        key_l = key.lower().rsplit(".", 1)[-1]
        if key_l in {"status", "state", "result", "validation", "email_status"}:
            value_s = str(value or "").strip()
            if delivery_confirmed and _SMTPBZ_VALID_TEXT_RE.match(value_s):
                return True, "ok_smtpbz", "", details

    if delivery_confirmed:
        return True, "ok_smtpbz", "", details
    return None


def _flatten_smtpbz_values(payload: Any, prefix: str = ""):
    if isinstance(payload, dict):
        for key, value in payload.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                yield from _flatten_smtpbz_values(value, name)
            else:
                yield name, value
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            yield from _flatten_smtpbz_values(item, f"{prefix}[{index}]")
    else:
        yield prefix or "value", payload


def _safe_response_preview(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(key): _safe_response_preview(value) for key, value in list(payload.items())[:20]}
    if isinstance(payload, list):
        return [_safe_response_preview(value) for value in payload[:20]]
    value = str(payload or "")
    return value[:500]


def _domain_has_mail_route(domain: str, *, timeout_seconds: float) -> tuple[bool, str, str, dict[str, Any]]:
    timeout_key = max(1, min(30, int(round(float(timeout_seconds or 3.0)))))
    result = _cached_domain_has_mail_route(domain, timeout_key)
    if result[1] == "domain_lookup_failed":
        # A resolver timeout is transient and must not become a process-long
        # negative cache entry. Clearing also lets the bounded retry perform a
        # real second lookup.
        _cached_domain_has_mail_route.cache_clear()
    return result


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
