"""Provider adapters for external statistics tests.

Each adapter queries the real provider API to get sent/delivery statistics
so we can compare them against the application's own statistics.

No provider credentials are required for Level 1–2 tests; they become
necessary only in Level 4 (reconciliation). All adapters degrade gracefully
when credentials are absent.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _http_get(url: str, *, headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    req = Request(url, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode(errors="replace")[:500]
        raise ProviderApiError(f"HTTP {exc.code} from {url}: {body}") from exc
    except URLError as exc:
        raise ProviderApiError(f"Network error calling {url}: {exc.reason}") from exc


def _http_post(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = Request(url, data=body, headers=h, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body_text = exc.read().decode(errors="replace")[:500]
        raise ProviderApiError(f"HTTP {exc.code} from {url}: {body_text}") from exc
    except URLError as exc:
        raise ProviderApiError(f"Network error calling {url}: {exc.reason}") from exc


class ProviderApiError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Provider event record (normalised)
# ---------------------------------------------------------------------------


@dataclass
class ProviderEvent:
    provider: str
    message_id: str          # task_id / message_id / job_id
    event_type: str          # delivered / opened / clicked / hard_bounced / etc.
    recipient: str
    occurred_at: str         # ISO string from provider
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# RuSender adapter
# ---------------------------------------------------------------------------

RUSENDER_API_BASE = "https://api.rusender.ru/api/v1"


class RuSenderAdapter:
    """Query RuSender API for message status and event history."""

    def __init__(self, api_key: str, base_url: str = RUSENDER_API_BASE) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def get_message_status(self, task_id: str) -> dict[str, Any]:
        """Get status for a single message by task_id."""
        if not self.api_key:
            return {}
        try:
            return _http_get(self._url(f"external-mails/{task_id}"), headers=self._headers())
        except ProviderApiError:
            return {}

    def get_send_stats(self, *, from_date: str | None = None, to_date: str | None = None) -> dict[str, Any]:
        """Get aggregate send statistics from RuSender dashboard API."""
        if not self.api_key:
            return {}
        params: dict[str, str] = {}
        if from_date:
            params["date_from"] = from_date
        if to_date:
            params["date_to"] = to_date
        qs = ("?" + urlencode(params)) if params else ""
        try:
            return _http_get(self._url(f"external-mails/stats{qs}"), headers=self._headers())
        except ProviderApiError:
            return {}

    def get_events(self, task_id: str) -> list[ProviderEvent]:
        """Return normalised events for a given task_id."""
        data = self.get_message_status(task_id)
        events: list[ProviderEvent] = []
        for raw in data.get("events") or []:
            if not isinstance(raw, dict):
                continue
            events.append(ProviderEvent(
                provider="rusender",
                message_id=task_id,
                event_type=str(raw.get("trigger") or raw.get("status") or ""),
                recipient=str(raw.get("email") or raw.get("recipient") or ""),
                occurred_at=str(raw.get("created_at") or raw.get("occurred_at") or ""),
                raw=raw,
            ))
        return events


# ---------------------------------------------------------------------------
# MailoPost adapter
# ---------------------------------------------------------------------------

MAILOPOST_API_BASE = "https://api.mailopost.ru/v1"


class MailoPostAdapter:
    """Query MailoPost API for message and campaign statistics."""

    def __init__(self, api_token: str, base_url: str = MAILOPOST_API_BASE) -> None:
        self.api_token = api_token.strip()
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def get_message_status(self, message_id: str) -> dict[str, Any]:
        """Get status for a single message by message_id."""
        if not self.api_token:
            return {}
        try:
            return _http_get(self._url(f"email/messages/{message_id}"), headers=self._headers())
        except ProviderApiError:
            return {}

    def get_events(self, message_id: str) -> list[ProviderEvent]:
        if not self.api_token:
            return []
        data = self.get_message_status(message_id)
        events: list[ProviderEvent] = []
        status = str(data.get("status") or data.get("state") or "")
        if status:
            events.append(ProviderEvent(
                provider="mailopost",
                message_id=message_id,
                event_type=status,
                recipient=str(data.get("to") or data.get("recipient") or ""),
                occurred_at=str(data.get("updated_at") or data.get("sent_at") or ""),
                raw=data,
            ))
        return events


# ---------------------------------------------------------------------------
# UniSender Go adapter
# ---------------------------------------------------------------------------

UNISENDER_GO_API_BASE = "https://goapi.unisender.ru/ru/transactional/api/v1"


class UniSenderGoAdapter:
    """Query UniSender Go API for transactional email status."""

    def __init__(self, api_key: str, base_url: str = UNISENDER_GO_API_BASE) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.api_key, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def get_email_status(self, job_id: str) -> dict[str, Any]:
        """Get status for a job_id (UniSender Go job)."""
        if not self.api_key:
            return {}
        try:
            return _http_post(
                self._url("email/status.json"),
                {"job_id": [job_id]},
                headers=self._headers(),
            )
        except ProviderApiError:
            return {}

    def get_events(self, job_id: str) -> list[ProviderEvent]:
        if not self.api_key:
            return []
        data = self.get_email_status(job_id)
        events: list[ProviderEvent] = []
        for item in data.get("status") or []:
            if not isinstance(item, dict):
                continue
            events.append(ProviderEvent(
                provider="unisender_go",
                message_id=job_id,
                event_type=str(item.get("status") or ""),
                recipient=str(item.get("email") or ""),
                occurred_at=str(item.get("last_event_time") or ""),
                raw=item,
            ))
        return events


# ---------------------------------------------------------------------------
# UniSender Classic adapter (polling-only)
# ---------------------------------------------------------------------------

UNISENDER_CLASSIC_API_BASE = "https://api.unisender.ru/ru/api"


class UniSenderClassicAdapter:
    """Query UniSender Classic checkEmail API for delivery status (polling)."""

    def __init__(self, api_key: str, base_url: str = UNISENDER_CLASSIC_API_BASE) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    def check_email(self, message_ids: list[str]) -> dict[str, str]:
        """Returns map of message_id → provider_status string."""
        if not self.api_key or not message_ids:
            return {}
        url = f"{self.base_url}/checkEmail"
        params = urlencode(
            {"format": "json", "api_key": self.api_key, "email_id": ",".join(message_ids)}
        )
        try:
            data = _http_get(f"{url}?{params}")
        except ProviderApiError:
            return {}
        result: dict[str, str] = {}
        for item in data.get("result") or []:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or "")
            status = str(item.get("status") or "")
            if mid:
                result[mid] = status
        return result

    def get_events(self, message_id: str) -> list[ProviderEvent]:
        statuses = self.check_email([message_id])
        status = statuses.get(message_id, "")
        if not status:
            return []
        return [ProviderEvent(
            provider="unisender_classic",
            message_id=message_id,
            event_type=status,
            recipient="",
            occurred_at=datetime.now().isoformat(timespec="seconds"),
            raw={"status": status},
        )]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_provider_adapter(
    transport: str,
    *,
    rusender_api_key: str = "",
    mailopost_api_token: str = "",
    unisender_api_key: str = "",
) -> RuSenderAdapter | MailoPostAdapter | UniSenderGoAdapter | UniSenderClassicAdapter | None:
    """Return the right adapter for the given transport name."""
    t = transport.lower()
    if t == "rusender":
        return RuSenderAdapter(rusender_api_key)
    if t == "mailopost":
        return MailoPostAdapter(mailopost_api_token)
    if t == "unisender":
        # UniSender Go vs Classic is detected by base_url at runtime; default to Go.
        return UniSenderGoAdapter(unisender_api_key)
    return None
