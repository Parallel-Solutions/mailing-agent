from __future__ import annotations

import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.generator.delivery.email_validation import _validate_email_syntax
from src.generator.delivery.smtp_providers import PROVIDER_PRESETS

DOMAIN_PRESET_MAP: dict[str, str] = {
    "gmail.com": "gmail",
    "googlemail.com": "gmail",
    "outlook.com": "outlook",
    "hotmail.com": "outlook",
    "live.com": "outlook",
    "msn.com": "outlook",
    "yandex.ru": "yandex",
    "ya.ru": "yandex",
    "yandex.com": "yandex",
    "yandex.by": "yandex",
    "yandex.kz": "yandex",
    "mail.ru": "mailru",
    "inbox.ru": "mailru",
    "list.ru": "mailru",
    "bk.ru": "mailru",
}

_GOOGLE_MX_MARKERS = ("google.com", "googlemail.com")
_OUTLOOK_MX_MARKERS = ("outlook.com", "protection.outlook.com")
_MAILRU_MX_MARKERS = ("mail.ru", "emx.mail.ru", "mxs.mail.ru")
_YANDEX_MX_MARKERS = ("yandex.ru", "yandex.net")

_THUNDERBIRD_AUTOCONFIG_URL = "https://autoconfig.thunderbird.net/v1.1/{domain}"
_THUNDERBIRD_TIMEOUT_SECONDS = 2
_MX_TIMEOUT_SECONDS = 2
_CUSTOM_DISCOVERY_BUDGET_SECONDS = 10


@dataclass(frozen=True)
class SmtpDiscoveryResult:
    provider: str
    host: str
    port: int
    use_ssl: bool
    use_starttls: bool
    source: str
    confidence: str


def parse_discover_email(email: str) -> tuple[str, str]:
    syntax = _validate_email_syntax(
        str(email or "").strip(),
        checked_at=datetime.now().isoformat(timespec="seconds"),
        mode="syntax",
    )
    if not syntax.is_valid:
        raise ValueError("Укажите корректный email.")
    return syntax.normalized_email, syntax.domain


def discover_smtp_settings(email: str) -> SmtpDiscoveryResult | None:
    candidates = discover_smtp_candidates(email)
    return candidates[0] if candidates else None


def discover_smtp_candidates(email: str) -> list[SmtpDiscoveryResult]:
    _, domain = parse_discover_email(email)
    preset = _discover_from_domain_preset(domain)
    if preset is not None and preset.confidence == "high":
        return [_normalize_discovery_result(preset)]

    seen: set[tuple[str, int, bool, bool]] = set()
    results: list[SmtpDiscoveryResult] = []

    mx_result = _discover_from_mx_hint(domain)
    if mx_result is not None:
        _append_discovery_result(results, seen, mx_result)
        if mx_result.confidence == "high":
            return results

    slow_sources: list[Callable[[], SmtpDiscoveryResult | None]] = [
        lambda: _discover_from_microsoft_autodiscover(domain),
        lambda: _discover_from_mozilla_autoconfig(domain),
        lambda: _discover_from_submission_srv(domain),
        lambda: _discover_from_thunderbird(domain),
    ]
    with ThreadPoolExecutor(max_workers=len(slow_sources)) as executor:
        futures = [executor.submit(source) for source in slow_sources]
        try:
            for future in as_completed(futures, timeout=_CUSTOM_DISCOVERY_BUDGET_SECONDS):
                result = future.result()
                if result is not None:
                    _append_discovery_result(results, seen, result)
        except TimeoutError:
            for future in futures:
                future.cancel()
    return results


def _append_discovery_result(
    results: list[SmtpDiscoveryResult],
    seen: set[tuple[str, int, bool, bool]],
    result: SmtpDiscoveryResult,
) -> None:
    normalized = _normalize_discovery_result(result)
    key = (normalized.host.lower(), normalized.port, normalized.use_ssl, normalized.use_starttls)
    if key in seen:
        return
    seen.add(key)
    results.append(normalized)


def result_to_dict(result: SmtpDiscoveryResult, *, email: str, domain: str) -> dict[str, Any]:
    return {
        "email": email,
        "domain": domain,
        "discovered": True,
        "provider": result.provider,
        "host": result.host,
        "port": result.port,
        "use_ssl": result.use_ssl,
        "use_starttls": result.use_starttls,
        "source": result.source,
        "confidence": result.confidence,
    }


def _discover_from_domain_preset(domain: str) -> SmtpDiscoveryResult | None:
    provider_id = DOMAIN_PRESET_MAP.get(domain)
    if not provider_id:
        return None
    return _result_from_preset(provider_id, source="preset", confidence="high")


def _discover_from_mx_hint(domain: str) -> SmtpDiscoveryResult | None:
    mx_hosts = _lookup_mx_hosts(domain, timeout=_MX_TIMEOUT_SECONDS)
    if not mx_hosts:
        return None

    joined = " ".join(mx_hosts)
    if any(marker in joined for marker in _GOOGLE_MX_MARKERS):
        return _result_from_preset("gmail", source="mx_hint", confidence="high")
    if any(marker in joined for marker in _OUTLOOK_MX_MARKERS):
        return _result_from_preset("outlook", source="mx_hint", confidence="high")
    if any(marker in joined for marker in _MAILRU_MX_MARKERS):
        return _result_from_preset("mailru", source="mx_hint", confidence="high")
    if any(marker in joined for marker in _YANDEX_MX_MARKERS):
        return _result_from_preset("yandex", source="mx_hint", confidence="high")
    return None


def _discover_from_microsoft_autodiscover(domain: str) -> SmtpDiscoveryResult | None:
    url = f"https://autodiscover.{domain}/autodiscover/autodiscover.xml"
    return _discover_from_autoconfig_url(url, source="microsoft_autodiscover", confidence="medium")


def _discover_from_mozilla_autoconfig(domain: str) -> SmtpDiscoveryResult | None:
    urls = (
        f"https://autoconfig.{domain}/mail/config-v1.1.xml",
        f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml",
    )
    for url in urls:
        result = _discover_from_autoconfig_url(url, source="mozilla_autoconfig", confidence="medium")
        if result is not None:
            return result
    return None


def _discover_from_submission_srv(domain: str) -> SmtpDiscoveryResult | None:
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return None

    resolver = dns.resolver.Resolver()
    resolver.timeout = _MX_TIMEOUT_SECONDS
    resolver.lifetime = _MX_TIMEOUT_SECONDS
    try:
        answers = resolver.resolve(f"_submission._tcp.{domain}", "SRV")
    except (
        dns.resolver.NXDOMAIN,
        dns.resolver.NoAnswer,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
        OSError,
    ):
        return None

    for answer in answers:
        target = str(getattr(answer, "target", "") or "").strip().rstrip(".").lower()
        if not target:
            continue
        port = int(getattr(answer, "port", 587) or 587)
        return SmtpDiscoveryResult(
            provider="custom",
            host=target,
            port=port,
            use_ssl=port == 465,
            use_starttls=port != 465,
            source="submission_srv",
            confidence="medium",
        )
    return None


def _discover_from_autoconfig_url(
    url: str,
    *,
    source: str,
    confidence: str,
) -> SmtpDiscoveryResult | None:
    try:
        request = Request(url, headers={"User-Agent": "mailing-agent-smtp-autodiscover/1.0"})
        with urlopen(request, timeout=_THUNDERBIRD_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        return None
    except (URLError, TimeoutError, OSError, ValueError):
        return None
    return _parse_autoconfig_payload(payload, source=source, confidence=confidence)


def _parse_autoconfig_payload(
    payload: bytes,
    *,
    source: str,
    confidence: str,
) -> SmtpDiscoveryResult | None:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    for server in root.iter("outgoingServer"):
        server_type = (server.attrib.get("type") or "smtp").strip().lower()
        if server_type not in {"", "smtp"}:
            continue
        hostname = _xml_text(server, "hostname")
        if not hostname:
            continue
        port_text = _xml_text(server, "port")
        port = int(port_text) if port_text and port_text.isdigit() else 587
        use_ssl, use_starttls = _socket_type_flags(_xml_text(server, "socketType"))
        return SmtpDiscoveryResult(
            provider="custom",
            host=hostname,
            port=port,
            use_ssl=use_ssl,
            use_starttls=use_starttls,
            source=source,
            confidence=confidence,
        )
    return None


def _discover_from_thunderbird(domain: str) -> SmtpDiscoveryResult | None:
    url = _THUNDERBIRD_AUTOCONFIG_URL.format(domain=domain)
    try:
        request = Request(url, headers={"User-Agent": "mailing-agent-smtp-autodiscover/1.0"})
        with urlopen(request, timeout=_THUNDERBIRD_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        return None
    except (URLError, TimeoutError, OSError, ValueError):
        return None

    return _parse_autoconfig_payload(payload, source="thunderbird", confidence="medium")


def _result_from_preset(provider_id: str, *, source: str, confidence: str) -> SmtpDiscoveryResult:
    preset = PROVIDER_PRESETS[provider_id]
    return SmtpDiscoveryResult(
        provider=preset.id,
        host=preset.host,
        port=preset.port,
        use_ssl=preset.use_ssl,
        use_starttls=preset.use_starttls,
        source=source,
        confidence=confidence,
    )


def _match_provider_by_host(host: str) -> str | None:
    normalized_host = str(host or "").strip().lower()
    if not normalized_host:
        return None
    for provider_id, preset in PROVIDER_PRESETS.items():
        if provider_id == "custom":
            continue
        if preset.host.lower() == normalized_host:
            return provider_id
    return None


def _normalize_discovery_result(result: SmtpDiscoveryResult) -> SmtpDiscoveryResult:
    if result.provider != "custom":
        return result
    matched_provider = _match_provider_by_host(result.host)
    if not matched_provider:
        return result
    return _result_from_preset(matched_provider, source=result.source, confidence=result.confidence)


def _lookup_mx_hosts(domain: str, *, timeout: int) -> list[str]:
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return []

    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    try:
        answers = resolver.resolve(domain, "MX")
    except (
        dns.resolver.NXDOMAIN,
        dns.resolver.NoAnswer,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
        OSError,
    ):
        return []

    hosts: list[str] = []
    for answer in answers:
        exchange = str(getattr(answer, "exchange", "") or "").strip().rstrip(".").lower()
        if exchange:
            hosts.append(exchange)
    return hosts


def _xml_text(parent: ET.Element, tag: str) -> str:
    node = parent.find(tag)
    if node is None or node.text is None:
        return ""
    return str(node.text).strip()


def _socket_type_flags(socket_type: str) -> tuple[bool, bool]:
    normalized = str(socket_type or "").strip().upper()
    if normalized == "SSL":
        return True, False
    if normalized == "STARTTLS":
        return False, True
    return False, False
