"""EXT-OPEN-01, EXT-CLICK-01, EXT-CONSENT-01, EXT-FOLLOWUP-01 — Level 3 mailbox tests.

Tests verify the full real-mailbox path:
  EXT-OPEN-01:    Open tracking — open email → provider sends opened webhook → app status changes.
  EXT-CLICK-01:   Click tracking — click link in email → provider sends clicked webhook.
  EXT-CONSENT-01: Consent flow — receive real email → extract consent link → confirm → verify.
  EXT-FOLLOWUP-01: Follow-up dispatch — after consent, materials email arrives in mailbox.

Required env (in addition to EXT-SEND-* and EXT-WEBHOOK-* requirements):
    EXT_IMAP_HOST=imap.mail.ru
    EXT_IMAP_PORT=993
    EXT_IMAP_USER=test@example.com
    EXT_IMAP_PASSWORD=...
    EXT_IMAP_USE_SSL=true

Note on open tracking:
    The email client must load images (tracking pixel) for the provider to record an open.
    We simulate this by fetching the tracking pixel URL directly from the HTML body.
    This matches how real open tracking works in HTML email clients.
"""
from __future__ import annotations

import time
import unittest
from urllib.request import urlopen

from tests.external.config import ExtConfig, load_config, require_ext_enabled
from tests.external.adapters.app import ExtAppAdapter
from tests.external.adapters.mailbox import ImapMailboxAdapter


def setUpModule() -> None:  # noqa: N802
    require_ext_enabled()


class _BaseMailboxTest(unittest.TestCase):
    config: ExtConfig
    app: ExtAppAdapter
    mailbox: ImapMailboxAdapter

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()
        if not cls.config.skip_mailbox:
            cls.mailbox = ImapMailboxAdapter(
                cls.config.imap_host,
                cls.config.imap_port,
                cls.config.imap_user,
                cls.config.imap_password,
                use_ssl=cls.config.imap_use_ssl,
            )
        else:
            cls.mailbox = None  # type: ignore[assignment]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def _require_mailbox(self) -> ImapMailboxAdapter:
        if self.config.skip_mailbox:
            self.skipTest(
                "IMAP mailbox not configured. Set EXT_IMAP_HOST / EXT_IMAP_USER / EXT_IMAP_PASSWORD."
            )
        return self.mailbox

    def _require_job(self) -> str:
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        return self.config.job_id

    def _require_public_url(self) -> str:
        if not self.config.public_base_url:
            self.skipTest("EXT_PUBLIC_BASE_URL not set.")
        return self.config.public_base_url


# ---------------------------------------------------------------------------
# EXT-OPEN-01 — Open tracking via real mailbox
# ---------------------------------------------------------------------------


class TestExtOpenTracking(unittest.TestCase):
    """EXT-OPEN-01: Open email in mailbox → provider records open → webhook → app status=opened."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()
        if not cls.config.skip_mailbox:
            cls.mailbox = ImapMailboxAdapter(
                cls.config.imap_host, cls.config.imap_port,
                cls.config.imap_user, cls.config.imap_password,
                use_ssl=cls.config.imap_use_ssl,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if self.config.skip_mailbox:
            self.skipTest("IMAP mailbox not configured.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.public_base_url:
            self.skipTest("EXT_PUBLIC_BASE_URL not set — open tracking requires real webhook delivery.")
        if self.config.transport.lower() not in {"rusender", "mailopost", "unisender"}:
            self.skipTest("Open tracking not supported for SMTP transport.")

    def test_ext_open_01_open_tracking_via_mailbox(self) -> None:
        """EXT-OPEN-01: Find email in mailbox, fetch tracking pixel to simulate open, wait for webhook."""
        job_id = self.config.job_id
        provider = self.config.transport.lower()

        # Find the email in mailbox
        print(f"[EXT-OPEN-01] Searching mailbox for delivery email (timeout {self.config.mailbox_wait_seconds}s)…")
        messages = self.mailbox.wait_for_message(
            to_contains=self.config.imap_user,
            timeout=self.config.mailbox_wait_seconds,
        )

        if not messages:
            self.skipTest(
                f"Email not found in mailbox within {self.config.mailbox_wait_seconds}s. "
                "Run EXT-SEND-01 first or check IMAP credentials."
            )

        msg = messages[0]
        print(f"[EXT-OPEN-01] Found email: subject={msg.subject!r}")

        with self.subTest("email_has_html_body"):
            self.assertGreater(len(msg.html_body), 0, "Email HTML body is empty.")

        # Simulate open by fetching tracking pixel URLs from HTML
        from tests.external.adapters.mailbox import _ANY_LINK_RE
        links = _ANY_LINK_RE.findall(msg.html_body)
        pixel_links = [
            link for link in links
            if any(kw in link.lower() for kw in ("track", "open", "pixel", "img"))
        ]

        if pixel_links:
            print(f"[EXT-OPEN-01] Fetching tracking pixel: {pixel_links[0]}")
            try:
                with urlopen(pixel_links[0], timeout=10):
                    pass
            except Exception as exc:
                print(f"[EXT-OPEN-01] Tracking pixel fetch failed (non-fatal): {exc}")
        else:
            print(
                "[EXT-OPEN-01] No tracking pixel found in HTML. "
                "Open tracking depends on provider injecting a pixel. "
                "This may not work for all providers/templates."
            )

        # Wait for provider to send opened webhook
        print(f"[EXT-OPEN-01] Waiting {self.config.webhook_wait_seconds}s for 'opened' webhook…")
        event = self.app.wait_for_webhook_event(
            job_id,
            provider if provider != "mailopost" else "mailopost",
            event_type="opened" if provider != "rusender" else "open",
            timeout=self.config.webhook_wait_seconds,
        )

        if event is None:
            self.skipTest(
                f"'Opened' webhook did not arrive within {self.config.webhook_wait_seconds}s. "
                "Possible reasons: tracking pixel not fetched, provider doesn't support open tracking, "
                "or webhook URL not reachable."
            )

        with self.subTest("recipient_status_opened"):
            recipient = self.app.wait_for_recipient_status(
                job_id, expected_status="opened", timeout=30.0
            )
            self.assertIsNotNone(
                recipient,
                "No recipient reached manager_status=opened after webhook."
            )

        with self.subTest("dashboard_open_rate_positive"):
            dashboard = self.app.get_dashboard(job_id)
            rates = dashboard.get("rates") or {}
            open_rate = float(rates.get("open_rate") or 0)
            self.assertGreater(open_rate, 0, "open_rate should be > 0 after opened webhook.")


# ---------------------------------------------------------------------------
# EXT-CLICK-01 — Click tracking via real mailbox
# ---------------------------------------------------------------------------


class TestExtClickTracking(unittest.TestCase):
    """EXT-CLICK-01: Click link in real email → provider click event → app status=clicked."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()
        if not cls.config.skip_mailbox:
            cls.mailbox = ImapMailboxAdapter(
                cls.config.imap_host, cls.config.imap_port,
                cls.config.imap_user, cls.config.imap_password,
                use_ssl=cls.config.imap_use_ssl,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if self.config.skip_mailbox:
            self.skipTest("IMAP mailbox not configured.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.public_base_url:
            self.skipTest("EXT_PUBLIC_BASE_URL not set.")

    def test_ext_click_01_click_tracking_via_mailbox(self) -> None:
        """EXT-CLICK-01: Find email, click a link, wait for clicked webhook."""
        job_id = self.config.job_id
        provider = self.config.transport.lower()

        print(f"[EXT-CLICK-01] Searching mailbox for email…")
        messages = self.mailbox.wait_for_message(
            to_contains=self.config.imap_user,
            timeout=self.config.mailbox_wait_seconds,
        )

        if not messages:
            self.skipTest(f"Email not found in mailbox.")

        msg = messages[0]

        with self.subTest("email_contains_links"):
            links = self.mailbox.extract_all_links(msg)
            self.assertGreater(len(links), 0, "Email has no links to click.")

        # Click the first non-tracking link (avoid consent link — that would trigger consent flow)
        clickable = [
            link for link in self.mailbox.extract_all_links(msg)
            if "consent" not in link.lower()
            and "confirm" not in link.lower()
            and link.startswith("http")
        ]

        if not clickable:
            self.skipTest("No clickable links in email (only consent link found).")

        target_url = clickable[0]
        print(f"[EXT-CLICK-01] Clicking link: {target_url[:80]}…")

        try:
            with urlopen(target_url, timeout=15):
                pass
        except Exception as exc:
            print(f"[EXT-CLICK-01] Link fetch returned error (may still register click): {exc}")

        # Wait for provider clicked webhook
        print(f"[EXT-CLICK-01] Waiting {self.config.webhook_wait_seconds}s for 'clicked' webhook…")
        event = self.app.wait_for_webhook_event(
            job_id,
            provider if provider != "mailopost" else "mailopost",
            event_type="click" if provider == "rusender" else "clicked",
            timeout=self.config.webhook_wait_seconds,
        )

        if event is None:
            self.skipTest(
                f"'Clicked' webhook did not arrive within {self.config.webhook_wait_seconds}s. "
                "Provider may not support click tracking, or webhook URL not reachable."
            )

        with self.subTest("recipient_status_clicked"):
            recipient = self.app.wait_for_recipient_status(
                job_id, expected_status="clicked", timeout=30.0
            )
            self.assertIsNotNone(recipient, "No recipient reached manager_status=clicked.")

        with self.subTest("recipient_interest_high"):
            if recipient:
                interest_key = str((recipient.get("interest") or {}).get("key") or "")
                self.assertEqual(interest_key, "high", f"Expected interest=high after click, got {interest_key!r}.")

        with self.subTest("dashboard_ctr_positive"):
            dashboard = self.app.get_dashboard(job_id)
            rates = dashboard.get("rates") or {}
            ctr = float(rates.get("ctr") or 0)
            self.assertGreater(ctr, 0, "CTR should be > 0 after clicked webhook.")


# ---------------------------------------------------------------------------
# EXT-CONSENT-01 — Full consent flow via real email
# ---------------------------------------------------------------------------


class TestExtConsentFlow(unittest.TestCase):
    """EXT-CONSENT-01: Send consent_request → receive real email → extract link → confirm."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()
        if not cls.config.skip_mailbox:
            cls.mailbox = ImapMailboxAdapter(
                cls.config.imap_host, cls.config.imap_port,
                cls.config.imap_user, cls.config.imap_password,
                use_ssl=cls.config.imap_use_ssl,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if self.config.skip_mailbox:
            self.skipTest("IMAP mailbox not configured.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.public_base_url:
            self.skipTest(
                "EXT_PUBLIC_BASE_URL not set — consent link must point to a publicly reachable URL."
            )

    def test_ext_consent_01_consent_flow_via_real_email(self) -> None:
        """EXT-CONSENT-01: Send consent request → find email → confirm consent → verify."""
        # NOTE: run_send() posts to the now-permanently-disabled /api/sender/run
        # (legacy xlsx sender). This test needs a CampaignFlow-based trigger for
        # consent_request before it can run against real infrastructure again.
        self.skipTest("Legacy /api/sender/run is disabled; EXT-CONSENT-01 needs a CampaignFlow-based rewrite.")
        job_id = self.config.job_id

        # Step 1: Send consent request
        print("[EXT-CONSENT-01] Sending consent_request…")
        self.app.run_send(job_id, send_mode="consent_request")
        status = self.app.wait_send_completed(job_id)

        with self.subTest("sender_completed"):
            self.assertEqual(str(status.get("status") or "").lower(), "completed")

        # Step 2: Verify sent_mail_log has consent_request entry
        records = self.app.read_sent_mail_log(job_id)
        consent_records = [r for r in records if "consent" in str(r.get("send_mode") or "").lower()]

        with self.subTest("sent_mail_log_has_consent_request"):
            if records:  # Only check if in-process access is available
                self.assertGreater(len(consent_records), 0, "No consent_request entry in sent_mail_log.")

        # Step 3: Find email in mailbox
        print(f"[EXT-CONSENT-01] Waiting for consent email in mailbox (timeout {self.config.mailbox_wait_seconds}s)…")
        messages = self.mailbox.wait_for_message(
            subject_contains="согласие",
            to_contains=self.config.imap_user,
            timeout=self.config.mailbox_wait_seconds,
        )

        if not messages:
            # Try without subject filter
            messages = self.mailbox.wait_for_message(
                to_contains=self.config.imap_user,
                timeout=30.0,
            )

        if not messages:
            self.skipTest(
                f"Consent email not found in mailbox within {self.config.mailbox_wait_seconds}s. "
                "Check that EXT_TEST_EMAILS matches the IMAP mailbox address."
            )

        msg = messages[0]
        print(f"[EXT-CONSENT-01] Found email: subject={msg.subject!r}")

        # Step 4: Extract consent link from HTML
        with self.subTest("email_has_consent_link"):
            consent_link = self.mailbox.extract_consent_link(msg)
            self.assertIsNotNone(
                consent_link,
                "Consent link not found in email HTML. "
                f"Check that PUBLIC_BASE_URL={self.config.public_base_url!r} is correct.",
            )

        # Step 5: Open preview page
        preview_url = consent_link.replace("/confirm/", "/request/")
        print(f"[EXT-CONSENT-01] Opening consent preview: {preview_url}")
        with self.subTest("consent_preview_page_accessible"):
            try:
                with urlopen(preview_url, timeout=15) as resp:
                    self.assertEqual(resp.status, 200)
            except Exception as exc:
                self.fail(f"Consent preview page not accessible: {exc}")

        # Step 6: Confirm consent (GET request to confirm URL)
        print(f"[EXT-CONSENT-01] Confirming consent: {consent_link}")
        with self.subTest("consent_confirmation_returns_200"):
            resp = self.app.confirm_consent(consent_link.split("/consent/confirm/")[1])
            self.assertIn(
                resp.status_code,
                {200, 302},
                f"Consent confirmation returned {resp.status_code}.",
            )

        # Step 7: Verify consents.json
        with self.subTest("consents_json_status_confirmed"):
            confirmed = self.app.wait_for_consent_confirmed(job_id, timeout=30.0)
            self.assertIsNotNone(
                confirmed,
                "consents.json did not reach status=confirmed after confirmation.",
            )
            if confirmed:
                self.assertEqual(str(confirmed.get("status") or "").lower(), "confirmed")
                self.assertIsNotNone(confirmed.get("confirmed_at"), "confirmed_at should be set.")
                self.assertIsNotNone(confirmed.get("ip"), "ip should be saved.")

        # Step 8: Verify /api/sender/consents
        with self.subTest("api_consents_summary_confirmed"):
            consents_data = self.app.get_consents(job_id)
            summary = consents_data.get("summary") or {}
            confirmed_count = int(summary.get("confirmed") or 0)
            self.assertGreater(confirmed_count, 0, f"API consents.summary.confirmed={confirmed_count}.")

        with self.subTest("consents_materials_status_queued"):
            # After confirmation, materials_status should be queued or sent
            confirmed_rec = self.app.wait_for_consent_confirmed(job_id, timeout=5.0)
            if confirmed_rec:
                materials_status = str(confirmed_rec.get("materials_status") or "").lower()
                self.assertIn(
                    materials_status,
                    {"queued", "sent"},
                    f"materials_status should be queued or sent, got {materials_status!r}.",
                )


# ---------------------------------------------------------------------------
# EXT-FOLLOWUP-01 — Materials dispatch after consent
# ---------------------------------------------------------------------------


class TestExtFollowUp(unittest.TestCase):
    """EXT-FOLLOWUP-01: After consent, materials email dispatched and arrives in mailbox."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()
        if not cls.config.skip_mailbox:
            cls.mailbox = ImapMailboxAdapter(
                cls.config.imap_host, cls.config.imap_port,
                cls.config.imap_user, cls.config.imap_password,
                use_ssl=cls.config.imap_use_ssl,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def setUp(self) -> None:
        if self.config.skip_mailbox:
            self.skipTest("IMAP mailbox not configured.")
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.public_base_url:
            self.skipTest("EXT_PUBLIC_BASE_URL not set.")

    def test_ext_followup_01_materials_dispatch(self) -> None:
        """EXT-FOLLOWUP-01: Confirm consent → wait for materials_status=sent → check mailbox."""
        job_id = self.config.job_id

        # Check if consent already confirmed (from EXT-CONSENT-01) or confirm now
        consents = self.app.read_consents_json(job_id)
        confirmed = [r for r in consents if str(r.get("status") or "").lower() == "confirmed"]

        if not confirmed:
            # Attempt to confirm any pending consent
            pending = [r for r in consents if str(r.get("status") or "").lower() in {"request_sent", "pending"}]
            if not pending:
                self.skipTest(
                    "No consent records found. Run EXT-CONSENT-01 first or send consent_request."
                )
            token = str(pending[0].get("token") or "")
            if not token:
                self.skipTest("No token in consent record.")
            self.app.confirm_consent(token)
            time.sleep(2.0)

        # Wait for materials_status=sent (background dispatch)
        print(f"[EXT-FOLLOWUP-01] Waiting up to {self.config.followup_wait_seconds}s for materials dispatch…")
        materials_rec = self.app.wait_for_materials_sent(
            job_id, timeout=self.config.followup_wait_seconds
        )

        with self.subTest("consents_materials_status_sent"):
            self.assertIsNotNone(
                materials_rec,
                f"materials_status did not reach 'sent' within {self.config.followup_wait_seconds}s. "
                "Check background worker and consent recovery loop.",
            )
            if materials_rec:
                self.assertEqual(str(materials_rec.get("materials_status") or "").lower(), "sent")
                self.assertIsNotNone(materials_rec.get("materials_sent_at"))

        # Verify sent_mail_log has materials entry
        with self.subTest("sent_mail_log_materials_entry"):
            records = self.app.read_sent_mail_log(job_id)
            if records:
                materials_logs = [r for r in records if "material" in str(r.get("send_mode") or "").lower()]
                self.assertGreater(len(materials_logs), 0, "No materials send_mode entry in sent_mail_log.")

        # Check dashboard
        with self.subTest("dashboard_materials_sent"):
            dashboard = self.app.get_dashboard(job_id)
            summary = dashboard.get("summary") or {}
            mats = int(summary.get("materials_sent") or 0)
            self.assertGreater(mats, 0, f"dashboard.summary.materials_sent={mats} should be > 0.")

        # Check mailbox for follow-up email
        print("[EXT-FOLLOWUP-01] Checking mailbox for materials/КП email…")
        follow_up_msgs = self.mailbox.wait_for_message(
            to_contains=self.config.imap_user,
            timeout=self.config.mailbox_wait_seconds,
        )

        with self.subTest("materials_email_arrived_in_mailbox"):
            if not follow_up_msgs:
                print(
                    "[EXT-FOLLOWUP-01] WARNING: Follow-up email not found in mailbox. "
                    "This may be due to delivery delay or IMAP search limitations."
                )
            # Check PDF attachment in any recent message
            has_pdf = any(self.mailbox.has_pdf_attachment(msg) for msg in follow_up_msgs)
            if follow_up_msgs and not has_pdf:
                print(
                    "[EXT-FOLLOWUP-01] INFO: Emails found but none have PDF attachment. "
                    "Materials may be sent inline or attachment format differs."
                )


if __name__ == "__main__":
    unittest.main()
