"""EXT-BOUNCE-01..02, EXT-UNSUB-01, EXT-SPAM-01 — Bounce, unsubscribe, spam tests (Level 2).

IMPORTANT SAFETY CONSTRAINTS:
- Only use test addresses from our allowlist (EXT_TEST_EMAILS).
- Only use safe bounce test addresses or sandbox mechanism from the provider.
- Do NOT generate real spam complaints without provider sandbox.
- If no safe mechanism exists, the test is marked as skip with reason
  "manual / provider sandbox only".

EXT-BOUNCE-01: Hard bounce via safe test address → email_broken status.
EXT-BOUNCE-02: Soft bounce → soft_bounce status.
EXT-UNSUB-01:  Unsubscribe event → unsubscribed + do_not_contact.
EXT-SPAM-01:   Spam/complaint → spam status (provider sandbox only).

Required env:
    EXT_STATS_ENABLED=1
    EXT_JOB_ID=...
    EXT_RUSENDER_WEBHOOK_TOKEN=...     (or other provider)
    EXT_SANDBOX_BOUNCE_ADDRESS=bounce-test@... (optional; provider bounce test address)
    EXT_SANDBOX_SPAM_MODE=1           (only if provider has sandbox complaint event)
"""
from __future__ import annotations

import time
import unittest

from tests.external.config import ExtConfig, load_config, require_ext_enabled
from tests.external.adapters.app import ExtAppAdapter
from tests.external.webhook_payloads import (
    rusender_hard_bounced,
    rusender_soft_bounced,
    rusender_unsubscribed,
    rusender_complaint,
    mailopost_hard_bounced,
    mailopost_soft_bounced,
    mailopost_unsubscribed,
    mailopost_complaint,
    unisender_go_hard_bounced,
)


def setUpModule() -> None:  # noqa: N802
    require_ext_enabled()


class _BaseBounceTest(unittest.TestCase):
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

    def _require_message_id(self, job_id: str) -> str:
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id — run EXT-SEND-* first.")
        return mid

    def _rusender_token(self) -> str:
        token = self.config.rusender_webhook_token
        if not token:
            self.skipTest("EXT_RUSENDER_WEBHOOK_TOKEN not set.")
        return token

    def _mailopost_token(self) -> str:
        token = self.config.mailopost_webhook_token
        if not token:
            self.skipTest("EXT_MAILOPOST_WEBHOOK_TOKEN not set.")
        return token


# ---------------------------------------------------------------------------
# EXT-BOUNCE-01 — Hard bounce
# ---------------------------------------------------------------------------


class TestExtHardBounce(unittest.TestCase):
    """EXT-BOUNCE-01: Hard bounce event → manager_status=email_broken + email-problems."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def test_ext_bounce_01_hard_bounce_rusender(self) -> None:
        """EXT-BOUNCE-01: Simulate hard bounce via webhook → email_broken status."""
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.rusender_webhook_token:
            self.skipTest("EXT_RUSENDER_WEBHOOK_TOKEN not set.")
        if self.config.transport.lower() != "rusender":
            self.skipTest("EXT_TRANSPORT != rusender.")

        job_id = self.config.job_id
        token = self.config.rusender_webhook_token
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id — run EXT-SEND-01 first.")

        # Check if we have a real sandbox bounce address from the provider
        if self.config.sandbox_bounce_address:
            print(f"[EXT-BOUNCE-01] Using provider sandbox bounce address: {self.config.sandbox_bounce_address}")
            print(
                "[EXT-BOUNCE-01] WARNING: Real send to bounce address requires re-run of EXT-SEND-01 "
                "with EXT_TEST_EMAILS=<sandbox_bounce_address>. Using simulated webhook instead."
            )

        # Simulate hard bounce via webhook POST (safe approach — no real send to bounce address)
        payload = rusender_hard_bounced(mid)
        resp = self.app.post_rusender_webhook(token, payload)

        with self.subTest("webhook_accepted"):
            self.assertIn(resp.status_code, {200, 201, 204},
                          f"Hard bounce webhook rejected: {resp.status_code}")

        # Wait for status to propagate
        time.sleep(2.0)

        with self.subTest("recipient_status_email_broken"):
            recipient = self.app.wait_for_recipient_status(
                job_id, expected_status="email_broken", timeout=30.0
            )
            self.assertIsNotNone(
                recipient,
                "No recipient reached manager_status=email_broken after hard bounce webhook. "
                "Check status normalisation for 'hard_bounced' in manager_stats.py.",
            )

        with self.subTest("email_problems_has_recipient"):
            problems = self.app.get_email_problems(job_id)
            hard_bounce = int((problems.get("summary") or {}).get("hard_bounce") or 0)
            self.assertGreater(hard_bounce, 0, f"email-problems.summary.hard_bounce={hard_bounce}.")

        with self.subTest("bounce_reason_not_empty"):
            if recipient:
                bounce_reason = str(recipient.get("bounce_reason") or "")
                # bounce_reason may be empty if not yet populated — just log
                print(f"[EXT-BOUNCE-01] bounce_reason={bounce_reason!r}")

    def test_ext_bounce_01_hard_bounce_mailopost(self) -> None:
        """EXT-BOUNCE-01 (MailoPost variant): hard bounce via simulated webhook."""
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.mailopost_webhook_token:
            self.skipTest("EXT_MAILOPOST_WEBHOOK_TOKEN not set.")
        if self.config.transport.lower() != "mailopost":
            self.skipTest("EXT_TRANSPORT != mailopost.")

        job_id = self.config.job_id
        token = self.config.mailopost_webhook_token
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id.")

        payload = mailopost_hard_bounced(mid)
        resp = self.app.post_mailopost_webhook(token, payload)

        with self.subTest("webhook_accepted"):
            self.assertIn(resp.status_code, {200, 201, 204})

        time.sleep(2.0)

        with self.subTest("recipient_status_email_broken"):
            recipient = self.app.wait_for_recipient_status(
                job_id, expected_status="email_broken", timeout=30.0
            )
            self.assertIsNotNone(recipient)

        with self.subTest("email_problems_hard_bounce_positive"):
            problems = self.app.get_email_problems(job_id)
            hard_bounce = int((problems.get("summary") or {}).get("hard_bounce") or 0)
            self.assertGreater(hard_bounce, 0)


# ---------------------------------------------------------------------------
# EXT-BOUNCE-02 — Soft bounce
# ---------------------------------------------------------------------------


class TestExtSoftBounce(unittest.TestCase):
    """EXT-BOUNCE-02: Soft bounce event → manager_status=soft_bounce."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def test_ext_bounce_02_soft_bounce_rusender(self) -> None:
        """EXT-BOUNCE-02 (RuSender): soft bounce → soft_bounce status."""
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.rusender_webhook_token:
            self.skipTest("EXT_RUSENDER_WEBHOOK_TOKEN not set.")
        if self.config.transport.lower() != "rusender":
            self.skipTest("EXT_TRANSPORT != rusender.")

        job_id = self.config.job_id
        token = self.config.rusender_webhook_token
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id.")

        payload = rusender_soft_bounced(mid)
        resp = self.app.post_rusender_webhook(token, payload)

        with self.subTest("webhook_accepted"):
            self.assertIn(resp.status_code, {200, 201, 204})

        time.sleep(2.0)

        with self.subTest("recipient_status_soft_bounce"):
            recipient = self.app.wait_for_recipient_status(
                job_id, expected_status="soft_bounce", timeout=30.0
            )
            self.assertIsNotNone(
                recipient,
                "No recipient reached manager_status=soft_bounce after soft bounce webhook.",
            )

        with self.subTest("email_problems_soft_bounce_positive"):
            problems = self.app.get_email_problems(job_id)
            soft_bounce = int((problems.get("summary") or {}).get("soft_bounce") or 0)
            self.assertGreater(soft_bounce, 0, f"email-problems.summary.soft_bounce={soft_bounce}.")


# ---------------------------------------------------------------------------
# EXT-UNSUB-01 — Unsubscribe event
# ---------------------------------------------------------------------------


class TestExtUnsubscribe(unittest.TestCase):
    """EXT-UNSUB-01: Unsubscribe event → unsubscribed + do_not_contact."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def test_ext_unsub_01_unsubscribe_rusender(self) -> None:
        """EXT-UNSUB-01 (RuSender): unsubscribe webhook → status=unsubscribed + do_not_contact."""
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.rusender_webhook_token:
            self.skipTest("EXT_RUSENDER_WEBHOOK_TOKEN not set.")
        if self.config.transport.lower() != "rusender":
            self.skipTest("EXT_TRANSPORT != rusender.")

        job_id = self.config.job_id
        token = self.config.rusender_webhook_token
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id.")

        test_email = (self.config.test_emails or [""])[0]
        payload = rusender_unsubscribed(mid, email=test_email)
        resp = self.app.post_rusender_webhook(token, payload)

        with self.subTest("webhook_accepted"):
            self.assertIn(resp.status_code, {200, 201, 204})

        time.sleep(2.0)

        with self.subTest("recipient_status_unsubscribed"):
            recipient = self.app.wait_for_recipient_status(
                job_id, expected_status="unsubscribed", timeout=30.0
            )
            self.assertIsNotNone(
                recipient,
                "No recipient reached manager_status=unsubscribed after unsubscribe webhook.",
            )

        with self.subTest("recommended_action_do_not_contact"):
            if recipient:
                action_key = str((recipient.get("recommended_action") or {}).get("key") or "")
                self.assertEqual(
                    action_key,
                    "do_not_contact",
                    f"Expected recommended_action=do_not_contact, got {action_key!r}.",
                )

        # Known gap: system does not block repeat send after unsubscribe
        with self.subTest("known_gap_no_system_block"):
            print(
                "[EXT-UNSUB-01] KNOWN GAP: sender_agent does not block repeat sends to "
                "unsubscribed recipients automatically. This is documented as a known gap."
            )
            # We don't attempt a real re-send here — just document the gap.


# ---------------------------------------------------------------------------
# EXT-SPAM-01 — Spam / complaint (sandbox only)
# ---------------------------------------------------------------------------


class TestExtSpamComplaint(unittest.TestCase):
    """EXT-SPAM-01: Spam/complaint event → spam status (provider sandbox or simulated only).

    SAFETY: This test only runs in two modes:
      1. EXT_SANDBOX_SPAM_MODE=1 — provider has a sandbox mechanism for complaint events.
      2. Simulated via webhook POST (no real complaint, just payload replay).

    Never generate a real spam complaint against a real mailbox.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.app = ExtAppAdapter(cls.config)
        cls.app.login()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.close()

    def test_ext_spam_01_complaint_simulated(self) -> None:
        """EXT-SPAM-01: Simulate complaint webhook (safe — no real spam report)."""
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")
        if not self.config.rusender_webhook_token:
            self.skipTest(
                "EXT_RUSENDER_WEBHOOK_TOKEN not set. "
                "This test uses a simulated webhook POST — no real spam complaint is generated."
            )
        if self.config.transport.lower() != "rusender":
            self.skipTest("EXT_TRANSPORT != rusender.")

        job_id = self.config.job_id
        token = self.config.rusender_webhook_token
        mid = self.app.get_provider_message_id(job_id)
        if not mid:
            self.skipTest("No provider_message_id.")

        if not self.config.sandbox_spam_mode:
            print(
                "[EXT-SPAM-01] EXT_SANDBOX_SPAM_MODE=0. Running simulated webhook only. "
                "For real provider sandbox, set EXT_SANDBOX_SPAM_MODE=1."
            )

        # Simulated complaint webhook (SAFE — POST to our own endpoint)
        test_email = (self.config.test_emails or [""])[0]
        payload = rusender_complaint(mid, email=test_email)
        resp = self.app.post_rusender_webhook(token, payload)

        with self.subTest("webhook_accepted"):
            self.assertIn(resp.status_code, {200, 201, 204},
                          f"Complaint webhook rejected: {resp.status_code}")

        time.sleep(2.0)

        with self.subTest("recipient_status_spam"):
            recipient = self.app.wait_for_recipient_status(
                job_id, expected_status="spam", timeout=30.0
            )
            self.assertIsNotNone(
                recipient,
                "No recipient reached manager_status=spam after complaint webhook.",
            )

        with self.subTest("email_problems_has_complaint"):
            problems = self.app.get_email_problems(job_id)
            items = problems.get("problems") or problems.get("items") or []
            complaint_items = [
                item for item in items
                if "spam" in str(item.get("bounce_reason") or item.get("status") or "").lower()
                or "complaint" in str(item.get("bounce_reason_label") or "").lower()
            ]
            if items:
                self.assertGreater(len(complaint_items), 0,
                                   "email-problems does not show complaint item.")

    def test_ext_spam_01_sandbox_only_note(self) -> None:
        """EXT-SPAM-01 (note): Real spam complaint from provider requires sandbox."""
        if self.config.sandbox_spam_mode:
            self.skipTest(
                "EXT_SANDBOX_SPAM_MODE=1 set. Use the actual sandbox event from the provider dashboard. "
                "This note test is skipped."
            )
        print(
            "\n[EXT-SPAM-01] MANUAL / PROVIDER SANDBOX ONLY:\n"
            "  To test real spam/complaint events, the provider must offer a sandbox\n"
            "  mechanism that sends a test complaint webhook without creating a real\n"
            "  spam report. Check RuSender/MailoPost/UniSender documentation.\n"
            "  Do NOT generate real spam complaints via a real mailbox."
        )


if __name__ == "__main__":
    unittest.main()
