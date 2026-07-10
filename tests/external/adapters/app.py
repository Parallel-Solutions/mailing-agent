"""Application API adapter for external statistics tests.

Extends the existing E2EApiClient with statistics-specific methods
needed for external integration tests (EXT-* test cases).
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from typing import Any

import httpx

from tests.e2e.api_client import E2EApiClient, E2EApiError
from tests.external.config import ExtConfig


SESSION_COOKIE_NAME = "mailing_agent_session"


class ExtAppAdapter(E2EApiClient):
    """E2EApiClient + statistics API methods for external tests."""

    def __init__(self, config: ExtConfig) -> None:
        from tests.e2e.config import E2EConfig
        e2e_cfg = E2EConfig(
            base_url=config.base_url,
            username=config.username,
            password=config.password,
            fixtures_dir=Path(__file__).resolve().parents[3] / "tests" / "e2e" / "fixtures",
            send_pause_seconds=2.0,
            parallel_jobs=1,
            documents_timeout_seconds=600.0,
            sender_timeout_seconds=config.sender_timeout_seconds,
            consent_timeout_seconds=config.followup_wait_seconds,
            out_dir=Path(__file__).resolve().parents[2] / "out",
            filter_work_type=None,
            filter_document_mode=None,
            filter_kp_variant=None,
            filter_send_mode=None,
            filter_recipient_strategy=None,
        )
        super().__init__(e2e_cfg)
        self.ext_config = config

    def login(self) -> None:
        """Log into the app, converting config/connectivity failures to skips.

        When the harness is enabled (``EXT_STATS_ENABLED=1``) but not fully
        configured (unreachable app, wrong/absent credentials), we skip the
        test class gracefully instead of surfacing a hard error.
        """
        try:
            super().login()
        except (E2EApiError, httpx.HTTPError, OSError) as exc:
            raise unittest.SkipTest(
                f"External tests: app login failed ({exc}). "
                "Configure a reachable E2E_BASE_URL and valid "
                "E2E_USERNAME/E2E_PASSWORD to run them."
            ) from exc

    # ------------------------------------------------------------------
    # Sender run helpers
    # ------------------------------------------------------------------

    def run_send(
        self,
        job_id: str,
        *,
        send_mode: str = "materials",
        transport: str | None = None,
        dry_run: bool = False,
        recipient_strategy: str = "all",
        work_type: str = "mngp_settlements",
    ) -> dict[str, Any]:
        t = transport or self.ext_config.transport
        return self.sender_run(
            job_id,
            dry_run=dry_run,
            send_mode=send_mode,
            recipient_strategy=recipient_strategy,
            work_type=work_type,
            transport=t,
        )

    def wait_send_completed(self, job_id: str) -> dict[str, Any]:
        return self.wait_sender(job_id, expect_dry_run=False)

    # ------------------------------------------------------------------
    # Statistics API
    # ------------------------------------------------------------------

    def get_dashboard(self, job_id: str) -> dict[str, Any]:
        payload = self._json(
            self._request("GET", "/api/sender/manager-dashboard", params={"job_id": job_id})
        )
        return self._result(payload)

    def get_campaigns(self, job_id: str | None = None) -> dict[str, Any]:
        params = {}
        if job_id:
            params["job_id"] = job_id
        payload = self._json(self._request("GET", "/api/sender/campaigns", params=params))
        return self._result(payload)

    def get_recipients(self, job_id: str, **kwargs: Any) -> dict[str, Any]:
        params = {"job_id": job_id, **kwargs}
        payload = self._json(self._request("GET", "/api/sender/recipients", params=params))
        return self._result(payload)

    def get_consents(self, job_id: str) -> dict[str, Any]:
        payload = self._json(
            self._request("GET", "/api/sender/consents", params={"job_id": job_id})
        )
        return self._result(payload)

    def get_email_problems(self, job_id: str) -> dict[str, Any]:
        payload = self._json(
            self._request("GET", "/api/sender/email-problems", params={"job_id": job_id})
        )
        return self._result(payload)

    def get_campaign_analytics(self, job_id: str) -> dict[str, Any]:
        payload = self._json(
            self._request("GET", f"/api/sender/campaign-analytics/{job_id}")
        )
        return self._result(payload)

    # ------------------------------------------------------------------
    # Webhook simulation (for Level 1–2 preflight / idempotency)
    # ------------------------------------------------------------------

    def post_rusender_webhook(self, token: str, payload: dict[str, Any]) -> httpx.Response:
        return self._request(
            "POST",
            f"/api/webhooks/rusender/{token}",
            json=payload,
        )

    def post_mailopost_webhook(self, token: str, payload: dict[str, Any]) -> httpx.Response:
        return self._request(
            "POST",
            f"/api/webhooks/mailopost/{token}",
            json=payload,
        )

    def post_unisender_go_webhook(self, token: str, payload: dict[str, Any]) -> httpx.Response:
        return self._request(
            "POST",
            f"/api/webhooks/unisender-go/{token}",
            json=payload,
        )

    # ------------------------------------------------------------------
    # Sent mail log (in-process access)
    # ------------------------------------------------------------------

    def read_sent_mail_log(self, job_id: str) -> list[dict[str, Any]]:
        """Read sent_mail_log for a job via the in-process module (requires shared filesystem)."""
        try:
            from src.jobs.job_docs import read_sent_mail_log
        except ImportError:
            return []
        items = read_sent_mail_log(job_id)
        return [dict(i) for i in items if isinstance(i, dict)]

    def get_provider_message_id(self, job_id: str, *, recipient: str | None = None) -> str | None:
        """Return the first non-empty provider_message_id from sent_mail_log."""
        for item in self.read_sent_mail_log(job_id):
            if recipient and recipient.lower() not in str(item.get("recipient") or "").lower():
                continue
            mid = (
                str(item.get("provider_message_id") or "").strip()
                or str((item.get("provider") or {}).get("message_id") or "").strip()
                or str((item.get("provider") or {}).get("task_id") or "").strip()
                or str((item.get("provider") or {}).get("job_id") or "").strip()
            )
            if mid:
                return mid
        return None

    def read_provider_events_jsonl(self, job_id: str, provider: str) -> list[dict[str, Any]]:
        """Read {job}/state/{provider}_events.jsonl via in-process module."""
        try:
            from src.jobs.storage import resolve_job_paths
            from src.jobs.json_store import read_jsonl
        except ImportError:
            return []
        paths = resolve_job_paths(job_id)
        filename = f"{provider}_events.jsonl"
        path = paths.root_dir / "state" / filename
        if not path.exists():
            return []
        return [dict(r) for r in read_jsonl(path) if isinstance(r, dict)]

    def read_consents_json(self, job_id: str) -> list[dict[str, Any]]:
        """Read consents.json via in-process module."""
        try:
            from src.generator.delivery.consent_store import _load_records
        except ImportError:
            return []
        return [dict(r) for r in _load_records(job_id) if isinstance(r, dict)]

    # ------------------------------------------------------------------
    # Poll helpers
    # ------------------------------------------------------------------

    def wait_for_webhook_event(
        self,
        job_id: str,
        provider: str,
        *,
        event_type: str,
        task_id: str | None = None,
        timeout: float = 180.0,
        poll_interval: float = 5.0,
    ) -> dict[str, Any] | None:
        """Poll *_events.jsonl until the expected event arrives."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = self.read_provider_events_jsonl(job_id, provider)
            for ev in events:
                etype = str(ev.get("event_type") or ev.get("provider_status") or "")
                if event_type.lower() in etype.lower():
                    if task_id is None or str(ev.get("task_id") or ev.get("message_id") or "") == task_id:
                        return ev
            time.sleep(poll_interval)
        return None

    def wait_for_recipient_status(
        self,
        job_id: str,
        *,
        expected_status: str,
        recipient: str | None = None,
        timeout: float = 180.0,
        poll_interval: float = 5.0,
    ) -> dict[str, Any] | None:
        """Poll /api/sender/recipients until a recipient has the expected manager_status."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.get_recipients(job_id)
            items = data.get("recipients") or data.get("items") or []
            for item in items:
                status_key = str((item.get("manager_status") or {}).get("key") or "")
                if status_key == expected_status:
                    if recipient is None or recipient.lower() in str(item.get("email") or "").lower():
                        return item
            time.sleep(poll_interval)
        return None

    def wait_for_consent_confirmed(
        self,
        job_id: str,
        *,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any] | None:
        """Poll consents.json until at least one record has status=confirmed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            records = self.read_consents_json(job_id)
            for rec in records:
                if str(rec.get("status") or "").lower() == "confirmed":
                    return rec
            time.sleep(poll_interval)
        return None

    def wait_for_materials_sent(
        self,
        job_id: str,
        *,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any] | None:
        """Poll consents.json until at least one record has materials_status=sent."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            records = self.read_consents_json(job_id)
            for rec in records:
                if str(rec.get("materials_status") or "").lower() == "sent":
                    return rec
            time.sleep(poll_interval)
        return None
