from __future__ import annotations

import smtplib
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any

from src.generator.delivery.smtp_autodiscover import (
    SmtpDiscoveryResult,
    discover_smtp_candidates,
    parse_discover_email,
)

_PROBE_TIMEOUT_SECONDS = 5
_PROBE_BUDGET_SECONDS = 12.0
_TRANSPORT_CANDIDATES: tuple[tuple[int, bool, bool], ...] = (
    (587, False, True),
    (465, True, False),
    (25, False, True),
)


@dataclass(frozen=True)
class ProbeAttempt:
    host: str
    port: int
    use_ssl: bool
    use_starttls: bool
    reachable: bool
    error: str = ""
    banner: str = ""


@dataclass
class ProbeResult:
    host: str
    port: int
    use_ssl: bool
    use_starttls: bool
    reachable: bool
    provider: str = "custom"
    source: str = ""
    confidence: str = ""
    banner: str = ""
    tried: list[ProbeAttempt] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "use_ssl": self.use_ssl,
            "use_starttls": self.use_starttls,
            "reachable": self.reachable,
            "provider": self.provider,
            "source": self.source,
            "confidence": self.confidence,
            "banner": self.banner,
            "tried": [
                {
                    "host": item.host,
                    "port": item.port,
                    "use_ssl": item.use_ssl,
                    "use_starttls": item.use_starttls,
                    "reachable": item.reachable,
                    "error": item.error,
                    "banner": item.banner,
                }
                for item in self.tried
            ],
        }


def probe_smtp_for_email(
    email: str,
    *,
    deadline: float | None = None,
) -> tuple[ProbeResult | None, list[SmtpDiscoveryResult]]:
    normalized_email, _domain = parse_discover_email(email)
    del normalized_email
    discoveries = discover_smtp_candidates(email)
    hosts: list[tuple[str, SmtpDiscoveryResult | None]] = []
    seen_hosts: set[str] = set()
    for discovery in discoveries:
        host = discovery.host.strip().lower()
        if host and host not in seen_hosts:
            seen_hosts.add(host)
            hosts.append((host, discovery))
    if not hosts:
        return None, discoveries

    probe_deadline = deadline if deadline is not None else (time.monotonic() + _PROBE_BUDGET_SECONDS)
    tried: list[ProbeAttempt] = []
    for host, discovery in hosts:
        for port, use_ssl, use_starttls in _transport_candidates_for_discovery(discovery):
            if time.monotonic() >= probe_deadline:
                break
            remaining = max(0.5, probe_deadline - time.monotonic())
            attempt = _probe_transport(
                host,
                port,
                use_ssl=use_ssl,
                use_starttls=use_starttls,
                timeout=min(_PROBE_TIMEOUT_SECONDS, remaining),
            )
            tried.append(attempt)
            if attempt.reachable:
                return (
                    ProbeResult(
                        host=host,
                        port=port,
                        use_ssl=use_ssl,
                        use_starttls=use_starttls,
                        reachable=True,
                        provider=discovery.provider if discovery else "custom",
                        source=discovery.source if discovery else "probe",
                        confidence=discovery.confidence if discovery else "low",
                        banner=attempt.banner,
                        tried=tried,
                    ),
                    discoveries,
                )
        if time.monotonic() >= probe_deadline:
            break
    if tried:
        last = tried[-1]
        return (
            ProbeResult(
                host=last.host,
                port=last.port,
                use_ssl=last.use_ssl,
                use_starttls=last.use_starttls,
                reachable=False,
                provider=discoveries[0].provider if discoveries else "custom",
                source=discoveries[0].source if discoveries else "probe",
                confidence=discoveries[0].confidence if discoveries else "low",
                tried=tried,
            ),
            discoveries,
        )
    return None, discoveries


def _transport_candidates_for_discovery(
    discovery: SmtpDiscoveryResult | None,
) -> list[tuple[int, bool, bool]]:
    candidates: list[tuple[int, bool, bool]] = []
    seen: set[tuple[int, bool, bool]] = set()
    if discovery is not None:
        preferred = (discovery.port, discovery.use_ssl, discovery.use_starttls)
        candidates.append(preferred)
        seen.add(preferred)
    for item in _TRANSPORT_CANDIDATES:
        if item not in seen:
            candidates.append(item)
            seen.add(item)
    return candidates


def _resolve_ipv4_addresses(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError:
        return []
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address and address not in addresses:
            addresses.append(address)
    return addresses


def _probe_transport(
    host: str,
    port: int,
    *,
    use_ssl: bool,
    use_starttls: bool,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> ProbeAttempt:
    if use_starttls:
        return _probe_starttls(host, port, timeout=timeout)
    addresses = _resolve_ipv4_addresses(host)
    if not addresses:
        return ProbeAttempt(
            host=host,
            port=port,
            use_ssl=use_ssl,
            use_starttls=use_starttls,
            reachable=False,
            error="no_ipv4_address",
        )

    last_error = ""
    for address in addresses:
        attempt = _probe_plain_or_ssl(
            host,
            address,
            port,
            use_ssl=use_ssl,
            timeout=timeout,
        )
        if attempt.reachable:
            return attempt
        last_error = attempt.error
    return ProbeAttempt(
        host=host,
        port=port,
        use_ssl=use_ssl,
        use_starttls=use_starttls,
        reachable=False,
        error=last_error or "unreachable",
    )


def _probe_starttls(host: str, port: int, *, timeout: float) -> ProbeAttempt:
    server: smtplib.SMTP | None = None
    try:
        server = smtplib.SMTP(host, port, timeout=timeout)
        code, greeting = server.ehlo()
        if code >= 400:
            return ProbeAttempt(
                host=host,
                port=port,
                use_ssl=False,
                use_starttls=True,
                reachable=False,
                error=f"ehlo_failed:{code}",
            )
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        banner = str(greeting or "").strip()
        server.quit()
        return ProbeAttempt(
            host=host,
            port=port,
            use_ssl=False,
            use_starttls=True,
            reachable=True,
            banner=banner,
        )
    except Exception as exc:
        if server is not None:
            try:
                server.close()
            except Exception:
                pass
        return ProbeAttempt(
            host=host,
            port=port,
            use_ssl=False,
            use_starttls=True,
            reachable=False,
            error=str(exc),
        )


def _probe_plain_or_ssl(
    host: str,
    address: str,
    port: int,
    *,
    use_ssl: bool,
    timeout: float,
) -> ProbeAttempt:
    if use_ssl:
        return _probe_implicit_ssl(host, address, port, timeout=timeout)
    return _probe_plain(host, address, port, timeout=timeout)


def _probe_implicit_ssl(host: str, address: str, port: int, *, timeout: float) -> ProbeAttempt:
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((address, port), timeout=timeout)
        sock.settimeout(timeout)
        context = ssl.create_default_context()
        wrapped = context.wrap_socket(sock, server_hostname=host)
        banner = _read_banner(wrapped)
        wrapped.close()
        return ProbeAttempt(
            host=host,
            port=port,
            use_ssl=True,
            use_starttls=False,
            reachable=True,
            banner=banner,
        )
    except Exception as exc:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        return ProbeAttempt(
            host=host,
            port=port,
            use_ssl=True,
            use_starttls=False,
            reachable=False,
            error=str(exc),
        )


def _probe_plain(host: str, address: str, port: int, *, timeout: float) -> ProbeAttempt:
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((address, port), timeout=timeout)
        sock.settimeout(timeout)
        banner = _read_banner(sock)
        sock.close()
        return ProbeAttempt(
            host=host,
            port=port,
            use_ssl=False,
            use_starttls=False,
            reachable=True,
            banner=banner,
        )
    except Exception as exc:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        return ProbeAttempt(
            host=host,
            port=port,
            use_ssl=False,
            use_starttls=False,
            reachable=False,
            error=str(exc),
        )


def _read_banner(connection: socket.socket) -> str:
    try:
        payload = connection.recv(512)
        if not payload:
            return ""
        return payload.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
