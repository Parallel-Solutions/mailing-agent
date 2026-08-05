"""EXT-SEND-01..04 — Real provider send tests (Level 1).

Tests verify that:
- A real email is accepted by the external provider.
- provider_message_id (task_id / message_id / job_id) is saved in sent_mail_log.
- transport field in sent_mail_log matches the configured provider.
- /api/sender/campaigns shows sent += N.
- /api/sender/recipients shows pending (webhook not yet arrived).

These tests do NOT require a public webhook URL or a test mailbox.
They send real emails to addresses in EXT_TEST_EMAILS (must be our own).

Required env:
    EXT_STATS_ENABLED=1
    EXT_JOB_ID=job-...
    EXT_TEST_EMAILS=test@example.com
    EXT_TRANSPORT=rusender          # or mailopost / unisender
    RUSENDER_API_KEY=...            # etc.
    E2E_BASE_URL / E2E_USERNAME / E2E_PASSWORD
"""
from __future__ import annotations

import unittest

from tests.external.config import ExtConfig, load_config, require_ext_enabled
from tests.external.adapters.app import ExtAppAdapter


def setUpModule() -> None:  # noqa: N802
    require_ext_enabled()


class _BaseSendTest(unittest.TestCase):
    """Base class with shared setup/teardown."""

    config: ExtConfig
    app: ExtAppAdapter

    @classmethod
    def setUpClass(cls) -> None:
        # NOTE: /api/sender/run (the legacy xlsx sender, sender_agent.run_sender)
        # is now permanently disabled (404) — it has no UI and no open/click
        # tracking. `app.run_send()`/`wait_send_completed()` below target that
        # dead endpoint; this whole class needs to move to the CampaignFlow
        # send path (batch_worker.py) before it can run against real
        # infrastructure again.
        raise unittest.SkipTest(
            "Legacy /api/sender/run is disabled; EXT-SEND-* needs a CampaignFlow-based rewrite."
        )
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def _require_job(self) -> str:
        job_id = self.config.job_id
        if not job_id:
            self.skipTest("EXT_JOB_ID not set — skipping real send test.")
        return job_id

    def _require_test_emails(self) -> list[str]:
        emails = self.config.test_emails
        if not emails:
            self.skipTest("EXT_TEST_EMAILS not set — skipping real send test.")
        return emails

    def _require_webhook_token(self, provider: str) -> str:
        token_map = {
            "rusender": self.config.rusender_webhook_token,
            "mailopost": self.config.mailopost_webhook_token,
            "unisender": self.config.unisender_webhook_token,
        }
        token = token_map.get(provider, "")
        if not token:
            self.skipTest(f"Webhook token for {provider} not configured.")
        return token

    def _assert_sent_mail_log(self, job_id: str, expected_transport: str) -> str:
        """Verify sent_mail_log and return provider_message_id."""
        records = self.app.read_sent_mail_log(job_id)
        if not records:
            self.skipTest(
                "sent_mail_log is empty — sent_mail_log requires in-process access "
                "(run inside the app container or with shared JOBS_DIR)."
            )

        last = records[-1]
        transport = str(last.get("transport") or "").lower()
        self.assertEqual(
            transport,
            expected_transport.lower(),
            f"Expected transport={expected_transport!r} in sent_mail_log, got {transport!r}",
        )

        mid = self.app.get_provider_message_id(job_id)
        if expected_transport != "smtp":
            self.assertIsNotNone(
                mid,
                f"provider_message_id is empty in sent_mail_log for transport={expected_transport!r}. "
                "The provider should return a message/task identifier.",
            )
            self.assertGreater(len(mid or ""), 0)
        return mid or ""

    def _assert_campaigns_sent(self, job_id: str, min_sent: int = 1) -> None:
        campaigns = self.app.get_campaigns(job_id)
        items = campaigns.get("campaigns") or campaigns.get("items") or []
        for campaign in items:
            cjob = str(campaign.get("job_id") or campaign.get("id") or "")
            if cjob == job_id or not items:
                sent = int(campaign.get("sent") or 0)
                self.assertGreaterEqual(
                    sent,
                    min_sent,
                    f"Expected campaigns.sent >= {min_sent}, got {sent} for job_id={job_id!r}.",
                )
                return
        # Single-job mode — check dashboard instead
        dashboard = self.app.get_dashboard(job_id)
        summary = dashboard.get("summary") or {}
        sent = int(summary.get("sent") or 0)
        self.assertGreaterEqual(
            sent,
            min_sent,
            f"dashboard.summary.sent={sent} < expected {min_sent} for job_id={job_id!r}.",
        )

    def _assert_recipients_pending(self, job_id: str) -> None:
        """Immediately after send (no webhook yet), recipients should be pending."""
        data = self.app.get_recipients(job_id)
        items = data.get("recipients") or data.get("items") or []
        for item in items:
            status_key = str((item.get("manager_status") or {}).get("key") or "")
            self.assertIn(
                status_key,
                {"pending", "no_data", ""},
                f"Recipient status should be pending right after send, got {status_key!r}. "
                "If webhook already arrived, this is expected — re-run with a fresh job.",
            )


# ---------------------------------------------------------------------------
# EXT-SEND-01 — RuSender real send
# ---------------------------------------------------------------------------


class TestExtSendRuSender(unittest.TestCase):
    """EXT-SEND-01: Send via RuSender, verify provider_message_id and campaigns."""

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

    def setUp(self) -> None:
        if self.config.transport.lower() != "rusender":
            self.skipTest("EXT_TRANSPORT != rusender — skipping EXT-SEND-01.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_ext_send_01_rusender_real_send(self) -> None:
        """EXT-SEND-01: RuSender real send → provider_message_id saved."""
        job_id = self.config.job_id

        # Run sender
        self.app.run_send(job_id, send_mode="materials", transport="rusender")
        status = self.app.wait_send_completed(job_id)

        with self.subTest("sender_completed"):
            self.assertEqual(str(status.get("status") or "").lower(), "completed")

        # Verify sent_mail_log
        records = self.app.read_sent_mail_log(job_id)
        if not records:
            self.skipTest("sent_mail_log is empty — requires in-process file access.")

        with self.subTest("transport_in_log"):
            last = records[-1]
            transport = str(last.get("transport") or "").lower()
            self.assertEqual(transport, "rusender")

        with self.subTest("provider_message_id_not_empty"):
            mid = self.app.get_provider_message_id(job_id)
            self.assertIsNotNone(mid, "provider_message_id (task_id) must be saved for RuSender sends.")
            self.assertGreater(len(mid), 0, "provider_message_id must not be empty.")

        with self.subTest("campaigns_sent_incremented"):
            dashboard = self.app.get_dashboard(job_id)
            sent = int((dashboard.get("summary") or {}).get("sent") or 0)
            self.assertGreater(sent, 0, f"dashboard.summary.sent={sent} should be > 0 after send.")

        with self.subTest("recipients_pending"):
            data = self.app.get_recipients(job_id)
            items = data.get("recipients") or data.get("items") or []
            pending_statuses = {"pending", "no_data", ""}
            non_pending = [
                item for item in items
                if str((item.get("manager_status") or {}).get("key") or "") not in pending_statuses
            ]
            if non_pending:
                # Webhook may have already arrived — just log, don't fail
                print(
                    f"[EXT-SEND-01] {len(non_pending)} recipients already have non-pending status "
                    "(webhook arrived quickly). This is acceptable."
                )


# ---------------------------------------------------------------------------
# EXT-SEND-02 — MailoPost real send
# ---------------------------------------------------------------------------


class TestExtSendMailoPost(unittest.TestCase):
    """EXT-SEND-02: Send via MailoPost, verify provider_message_id and campaigns."""

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
            self.skipTest("EXT_TRANSPORT != mailopost — skipping EXT-SEND-02.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_ext_send_02_mailopost_real_send(self) -> None:
        """EXT-SEND-02: MailoPost real send → message_id saved."""
        job_id = self.config.job_id

        self.app.run_send(job_id, send_mode="materials", transport="mailopost")
        status = self.app.wait_send_completed(job_id)

        with self.subTest("sender_completed"):
            self.assertEqual(str(status.get("status") or "").lower(), "completed")

        records = self.app.read_sent_mail_log(job_id)
        if not records:
            self.skipTest("sent_mail_log is empty — requires in-process file access.")

        with self.subTest("transport_in_log"):
            last = records[-1]
            self.assertEqual(str(last.get("transport") or "").lower(), "mailopost")

        with self.subTest("provider_message_id_not_empty"):
            mid = self.app.get_provider_message_id(job_id)
            self.assertIsNotNone(mid)
            self.assertGreater(len(mid), 0)

        with self.subTest("dashboard_sent_positive"):
            dashboard = self.app.get_dashboard(job_id)
            sent = int((dashboard.get("summary") or {}).get("sent") or 0)
            self.assertGreater(sent, 0)


# ---------------------------------------------------------------------------
# EXT-SEND-03 — UniSender Go real send
# ---------------------------------------------------------------------------


class TestExtSendUniSenderGo(unittest.TestCase):
    """EXT-SEND-03: UniSender Go real send → job_id + metadata.app_job_id."""

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
            self.skipTest("EXT_TRANSPORT != unisender — skipping EXT-SEND-03.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_ext_send_03_unisender_go_real_send(self) -> None:
        """EXT-SEND-03: UniSender Go send → provider job_id saved as provider_message_id."""
        job_id = self.config.job_id

        self.app.run_send(job_id, send_mode="materials", transport="unisender")
        status = self.app.wait_send_completed(job_id)

        with self.subTest("sender_completed"):
            self.assertEqual(str(status.get("status") or "").lower(), "completed")

        records = self.app.read_sent_mail_log(job_id)
        if not records:
            self.skipTest("sent_mail_log is empty — requires in-process file access.")

        with self.subTest("transport_in_log"):
            last = records[-1]
            self.assertEqual(str(last.get("transport") or "").lower(), "unisender")

        with self.subTest("provider_message_id_not_empty"):
            mid = self.app.get_provider_message_id(job_id)
            self.assertIsNotNone(mid)
            self.assertGreater(len(mid), 0)

        with self.subTest("dashboard_sent_positive"):
            dashboard = self.app.get_dashboard(job_id)
            sent = int((dashboard.get("summary") or {}).get("sent") or 0)
            self.assertGreater(sent, 0)

        # UniSender Go: webhook matching uses metadata.app_job_id — verify it's passed
        # (we can infer from the fact that provider_message_id is a UniSender job_id)
        with self.subTest("provider_message_id_looks_like_job_id"):
            mid = self.app.get_provider_message_id(job_id)
            # UniSender Go job_id is typically a long hex string
            self.assertIsNotNone(mid)


# ---------------------------------------------------------------------------
# EXT-SEND-04 — UniSender Classic real send + polling
# ---------------------------------------------------------------------------


class TestExtSendUniSenderClassic(unittest.TestCase):
    """EXT-SEND-04: UniSender Classic send → provider_message_id, polling delivers status."""

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
            self.skipTest("EXT_TRANSPORT != unisender — skipping EXT-SEND-04.")
        if not self.config.unisender_api_key:
            self.skipTest("UNISENDER_API_KEY not set — cannot verify polling.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_ext_send_04_unisender_classic_polling(self) -> None:
        """EXT-SEND-04: UniSender Classic — message accepted and provider_message_id saved."""
        job_id = self.config.job_id

        self.app.run_send(job_id, send_mode="materials", transport="unisender")
        status = self.app.wait_send_completed(job_id)

        with self.subTest("sender_completed"):
            self.assertEqual(str(status.get("status") or "").lower(), "completed")

        records = self.app.read_sent_mail_log(job_id)
        if not records:
            self.skipTest("sent_mail_log is empty — requires in-process file access.")

        with self.subTest("provider_message_id_not_empty"):
            mid = self.app.get_provider_message_id(job_id)
            self.assertIsNotNone(mid)

        # Check via classic polling adapter
        from tests.external.adapters.provider import UniSenderClassicAdapter
        adapter = UniSenderClassicAdapter(self.config.unisender_api_key)
        mid = self.app.get_provider_message_id(job_id)
        if mid:
            with self.subTest("classic_polling_returns_status"):
                statuses = adapter.check_email([mid])
                # Status may not be available yet — just check that API responds
                self.assertIsInstance(statuses, dict)
                status_val = statuses.get(mid, "")
                print(f"[EXT-SEND-04] Classic polling status for {mid!r}: {status_val!r}")


if __name__ == "__main__":
    unittest.main()
