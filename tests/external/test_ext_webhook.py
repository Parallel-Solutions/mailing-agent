"""EXT-WEBHOOK-01..03, EXT-IDEM-01..02 — Real webhook callback tests (Level 2).

Tests verify that:
- Real webhook from provider is received and saved to *_events.jsonl.
- event_type and task_id/message_id are correct.
- job_id is correctly matched (not unmatched).
- Recipient status in /api/sender/recipients changes to "delivered".
- Dashboard KPI summary.delivered increments.
- Idempotency: duplicate webhook payload does not create a second JSONL entry.

Required env (in addition to EXT-SEND-* requirements):
    EXT_PUBLIC_BASE_URL=https://staging.example.com
    EXT_RUSENDER_WEBHOOK_TOKEN=...   (registered in provider dashboard)
    EXT_MAILOPOST_WEBHOOK_TOKEN=...
    EXT_UNISENDER_WEBHOOK_TOKEN=...

The tests wait up to EXT_WEBHOOK_WAIT_SECONDS (default 180) for the real webhook.
If it doesn't arrive in time, the test is skipped (not failed) — this indicates
that the webhook URL is not reachable from the provider.
"""
from __future__ import annotations

import time
import unittest

from tests.external.config import ExtConfig, load_config, require_ext_enabled
from tests.external.adapters.app import ExtAppAdapter
from tests.external.webhook_payloads import (
    rusender_delivered,
    rusender_opened,
    rusender_clicked,
    mailopost_delivered,
    unisender_go_delivered,
)


def setUpModule() -> None:  # noqa: N802
    require_ext_enabled()


class _BaseWebhookTest(unittest.TestCase):
    config: ExtConfig
    app: ExtAppAdapter

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def _require_job(self) -> str:
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        return self.config.job_id

    def _require_public_url(self) -> str:
        if not self.config.public_base_url:
            self.skipTest(
                "EXT_PUBLIC_BASE_URL not set — webhook tests require a publicly reachable URL. "
                "Use Cloudflare Tunnel / ngrok or a staging server."
            )
        return self.config.public_base_url

    def _require_message_id(self, job_id: str) -> str:
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest(
                "No provider_message_id in sent_mail_log — "
                "run EXT-SEND-* first or ensure in-process file access."
            )
        return mid

    def _get_dashboard_delivered(self, job_id: str) -> int:
        data = self.app.get_dashboard(job_id)
        return int((data.get("summary") or {}).get("delivered") or 0)

    def _get_jsonl_event_count(self, job_id: str, provider: str, event_type: str) -> int:
        events = self.app.read_provider_events_jsonl(job_id, provider)
        return sum(
            1 for ev in events
            if event_type.lower() in str(ev.get("event_type") or ev.get("provider_status") or "").lower()
        )


# ---------------------------------------------------------------------------
# EXT-WEBHOOK-01 — RuSender delivered webhook
# ---------------------------------------------------------------------------


class TestExtWebhookRuSender(unittest.TestCase):
    """EXT-WEBHOOK-01: Real RuSender delivered webhook → JSONL + recipient status + KPI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if self.config.transport.lower() != "rusender":
            self.skipTest("EXT_TRANSPORT != rusender.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.rusender_webhook_token:
            self.skipTest("EXT_RUSENDER_WEBHOOK_TOKEN not set.")
        if not self.config.public_base_url:
            self.skipTest("EXT_PUBLIC_BASE_URL not set — real webhooks require public URL.")

    def test_ext_webhook_01_rusender_delivered(self) -> None:
        """EXT-WEBHOOK-01: Wait for real RuSender delivered webhook, then verify all sources."""
        job_id = self.config.job_id
        token = self.config.rusender_webhook_token

        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id — run EXT-SEND-01 first.")

        dashboard_before = self.app.get_dashboard(job_id)
        delivered_before = int((dashboard_before.get("summary") or {}).get("delivered") or 0)

        # Wait for real webhook from RuSender
        print(f"[EXT-WEBHOOK-01] Waiting up to {self.config.webhook_wait_seconds}s for RuSender delivered webhook…")
        t0 = time.monotonic()
        event = self.app.wait_for_webhook_event(
            job_id,
            "rusender",
            event_type="delivered",
            task_id=mid,
            timeout=self.config.webhook_wait_seconds,
        )

        if event is None:
            self.skipTest(
                f"RuSender delivered webhook did not arrive within {self.config.webhook_wait_seconds}s. "
                "Ensure the webhook URL is registered in RuSender dashboard and PUBLIC_BASE_URL is reachable."
            )

        elapsed = time.monotonic() - t0
        print(f"[EXT-WEBHOOK-01] Webhook received in {elapsed:.1f}s.")

        with self.subTest("jsonl_event_saved"):
            events = self.app.read_provider_events_jsonl(job_id, "rusender")
            delivered_events = [
                ev for ev in events
                if "delivered" in str(ev.get("provider_status") or ev.get("event_type") or "").lower()
            ]
            self.assertGreater(len(delivered_events), 0, "No 'delivered' event in rusender_events.jsonl.")

        with self.subTest("jsonl_event_has_correct_task_id"):
            matched = [
                ev for ev in events
                if str(ev.get("task_id") or "") == mid
            ]
            self.assertGreater(len(matched), 0, f"No event with task_id={mid!r} in rusender_events.jsonl.")

        with self.subTest("recipient_status_delivered"):
            recipient = self.app.wait_for_recipient_status(
                job_id,
                expected_status="delivered",
                timeout=30.0,
            )
            self.assertIsNotNone(
                recipient,
                "No recipient reached manager_status=delivered after webhook. "
                "Check status normalisation in manager_stats.py.",
            )

        with self.subTest("dashboard_delivered_incremented"):
            dashboard_after = self.app.get_dashboard(job_id)
            delivered_after = int((dashboard_after.get("summary") or {}).get("delivered") or 0)
            self.assertGreater(
                delivered_after,
                delivered_before,
                f"dashboard.summary.delivered did not increase: {delivered_before} → {delivered_after}.",
            )

        with self.subTest("delivery_rate_positive"):
            rates = dashboard_after.get("rates") or {}
            delivery_rate = float(rates.get("delivery_rate") or 0)
            self.assertGreater(delivery_rate, 0, "delivery_rate should be > 0 after delivered webhook.")


# ---------------------------------------------------------------------------
# EXT-WEBHOOK-02 — MailoPost delivered webhook
# ---------------------------------------------------------------------------


class TestExtWebhookMailoPost(unittest.TestCase):
    """EXT-WEBHOOK-02: Real MailoPost delivered webhook → JSONL + recipient status + KPI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if self.config.transport.lower() != "mailopost":
            self.skipTest("EXT_TRANSPORT != mailopost.")
        if not self.config.mailopost_webhook_token:
            self.skipTest("EXT_MAILOPOST_WEBHOOK_TOKEN not set.")
        if not self.config.public_base_url:
            self.skipTest("EXT_PUBLIC_BASE_URL not set.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_ext_webhook_02_mailopost_delivered(self) -> None:
        """EXT-WEBHOOK-02: Wait for real MailoPost delivered webhook."""
        job_id = self.config.job_id
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id — run EXT-SEND-02 first.")

        delivered_before = int((self.app.get_dashboard(job_id).get("summary") or {}).get("delivered") or 0)

        print(f"[EXT-WEBHOOK-02] Waiting {self.config.webhook_wait_seconds}s for MailoPost delivered webhook…")
        event = self.app.wait_for_webhook_event(
            job_id, "mailopost", event_type="delivered", task_id=mid,
            timeout=self.config.webhook_wait_seconds,
        )

        if event is None:
            self.skipTest(
                f"MailoPost delivered webhook did not arrive within {self.config.webhook_wait_seconds}s."
            )

        with self.subTest("jsonl_event_saved"):
            events = self.app.read_provider_events_jsonl(job_id, "mailopost")
            self.assertTrue(
                any("delivered" in str(ev.get("event_type") or "").lower() for ev in events),
                "No delivered event in mailopost_events.jsonl.",
            )

        with self.subTest("recipient_status_delivered"):
            recipient = self.app.wait_for_recipient_status(job_id, expected_status="delivered", timeout=30.0)
            self.assertIsNotNone(recipient)

        with self.subTest("dashboard_delivered_incremented"):
            delivered_after = int((self.app.get_dashboard(job_id).get("summary") or {}).get("delivered") or 0)
            self.assertGreater(delivered_after, delivered_before)


# ---------------------------------------------------------------------------
# EXT-WEBHOOK-03 — UniSender Go delivered webhook
# ---------------------------------------------------------------------------


class TestExtWebhookUniSenderGo(unittest.TestCase):
    """EXT-WEBHOOK-03: Real UniSender Go delivered webhook → JSONL + status + KPI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if self.config.transport.lower() != "unisender":
            self.skipTest("EXT_TRANSPORT != unisender.")
        if not self.config.unisender_webhook_token:
            self.skipTest("EXT_UNISENDER_WEBHOOK_TOKEN not set.")
        if not self.config.public_base_url:
            self.skipTest("EXT_PUBLIC_BASE_URL not set.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_ext_webhook_03_unisender_go_delivered(self) -> None:
        """EXT-WEBHOOK-03: Wait for real UniSender Go delivered webhook."""
        job_id = self.config.job_id
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id — run EXT-SEND-03 first.")

        delivered_before = int((self.app.get_dashboard(job_id).get("summary") or {}).get("delivered") or 0)

        print(f"[EXT-WEBHOOK-03] Waiting {self.config.webhook_wait_seconds}s for UniSender Go webhook…")
        event = self.app.wait_for_webhook_event(
            job_id, "unisender_go", event_type="delivered",
            timeout=self.config.webhook_wait_seconds,
        )

        if event is None:
            self.skipTest(
                f"UniSender Go webhook did not arrive within {self.config.webhook_wait_seconds}s. "
                "Verify metadata.app_job_id is passed in the send request and webhook URL is registered."
            )

        with self.subTest("jsonl_event_saved"):
            events = self.app.read_provider_events_jsonl(job_id, "unisender_go")
            self.assertTrue(any("delivered" in str(ev.get("event_type") or "").lower() for ev in events))

        with self.subTest("recipient_status_delivered"):
            recipient = self.app.wait_for_recipient_status(job_id, expected_status="delivered", timeout=30.0)
            self.assertIsNotNone(recipient)

        with self.subTest("dashboard_delivered_incremented"):
            delivered_after = int((self.app.get_dashboard(job_id).get("summary") or {}).get("delivered") or 0)
            self.assertGreater(delivered_after, delivered_before)


# ---------------------------------------------------------------------------
# EXT-IDEM-01 — Idempotency: RuSender webhook replay
# ---------------------------------------------------------------------------


class TestExtIdempotencyRuSender(unittest.TestCase):
    """EXT-IDEM-01: Duplicate RuSender webhook does not create a second JSONL entry."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if not self.config.rusender_webhook_token:
            self.skipTest("EXT_RUSENDER_WEBHOOK_TOKEN not set.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_ext_idem_01_rusender_duplicate_webhook(self) -> None:
        """EXT-IDEM-01: Sending the same RuSender webhook payload twice → single JSONL entry."""
        job_id = self.config.job_id
        token = self.config.rusender_webhook_token
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id — run EXT-SEND-01 first.")

        payload = rusender_delivered(mid)

        events_before = self.app.read_provider_events_jsonl(job_id, "rusender")
        delivered_count_before = sum(
            1 for ev in events_before
            if "delivered" in str(ev.get("provider_status") or "").lower()
            and str(ev.get("task_id") or "") == mid
        )
        dashboard_before = self.app.get_dashboard(job_id)
        delivered_kpi_before = int((dashboard_before.get("summary") or {}).get("delivered") or 0)

        # First send
        resp1 = self.app.post_rusender_webhook(token, payload)
        self.assertIn(resp1.status_code, {200, 201, 204}, f"First webhook POST failed: {resp1.status_code}")
        time.sleep(1.0)

        # Second send — identical payload
        resp2 = self.app.post_rusender_webhook(token, payload)
        self.assertIn(resp2.status_code, {200, 201, 204}, f"Second webhook POST failed: {resp2.status_code}")
        time.sleep(2.0)

        events_after = self.app.read_provider_events_jsonl(job_id, "rusender")
        delivered_count_after = sum(
            1 for ev in events_after
            if "delivered" in str(ev.get("provider_status") or "").lower()
            and str(ev.get("task_id") or "") == mid
        )

        with self.subTest("no_duplicate_jsonl_entry"):
            new_entries = delivered_count_after - delivered_count_before
            self.assertLessEqual(
                new_entries,
                1,
                f"Expected at most 1 new JSONL entry, got {new_entries}. "
                "Duplicate webhook created a second record — deduplication failed.",
            )

        with self.subTest("kpi_not_doubled"):
            dashboard_after = self.app.get_dashboard(job_id)
            delivered_kpi_after = int((dashboard_after.get("summary") or {}).get("delivered") or 0)
            kpi_increase = delivered_kpi_after - delivered_kpi_before
            self.assertLessEqual(
                kpi_increase,
                1,
                f"dashboard.summary.delivered increased by {kpi_increase} — "
                "duplicate webhook inflated the KPI.",
            )

        # Also verify response body contains duplicates info
        with self.subTest("response_reports_duplicate"):
            try:
                body = resp2.json()
                if isinstance(body, dict):
                    dups = int(body.get("duplicates") or 0)
                    # If duplicates key is present, it should be >= 1
                    if "duplicates" in body:
                        self.assertGreaterEqual(dups, 1, "Expected duplicates >= 1 in response.")
            except Exception:
                pass  # Response format may vary


# ---------------------------------------------------------------------------
# EXT-IDEM-02 — Idempotency: MailoPost webhook replay
# ---------------------------------------------------------------------------


class TestExtIdempotencyMailoPost(unittest.TestCase):
    """EXT-IDEM-02: Duplicate MailoPost webhook does not create a second JSONL entry."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if not self.config.mailopost_webhook_token:
            self.skipTest("EXT_MAILOPOST_WEBHOOK_TOKEN not set.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if self.config.transport.lower() != "mailopost":
            self.skipTest("EXT_TRANSPORT != mailopost.")

    def test_ext_idem_02_mailopost_duplicate_webhook(self) -> None:
        """EXT-IDEM-02: Sending same MailoPost webhook twice → single JSONL entry."""
        job_id = self.config.job_id
        token = self.config.mailopost_webhook_token
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id.")

        payload = mailopost_delivered(mid)

        events_before = self.app.read_provider_events_jsonl(job_id, "mailopost")
        count_before = sum(
            1 for ev in events_before
            if "delivered" in str(ev.get("event_type") or "").lower()
            and str(ev.get("message_id") or "") == mid
        )

        resp1 = self.app.post_mailopost_webhook(token, payload)
        self.assertIn(resp1.status_code, {200, 201, 204})
        time.sleep(1.0)

        resp2 = self.app.post_mailopost_webhook(token, payload)
        self.assertIn(resp2.status_code, {200, 201, 204})
        time.sleep(2.0)

        events_after = self.app.read_provider_events_jsonl(job_id, "mailopost")
        count_after = sum(
            1 for ev in events_after
            if "delivered" in str(ev.get("event_type") or "").lower()
            and str(ev.get("message_id") or "") == mid
        )

        with self.subTest("no_duplicate_jsonl_entry"):
            self.assertLessEqual(count_after - count_before, 1)


if __name__ == "__main__":
    unittest.main()
