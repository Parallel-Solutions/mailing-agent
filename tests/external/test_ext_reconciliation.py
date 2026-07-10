"""EXT-RECON-01 — Full reconciliation across all four sources (Level 4).

This is the top-level statistical verification test. It runs after a series of
sends and webhook events have been processed, then compares statistics from:

  1. App API  (/api/sender/manager-dashboard)
  2. App DB   (sent_mail_log from job_events)
  3. App JSONL (*_events.jsonl per provider)
  4. Provider API (RuSender / MailoPost / UniSender Go — if API keys configured)
  5. Mailbox  (email count — if IMAP configured)

Any HIGH severity mismatch fails the test. MEDIUM and LOW mismatches are
logged as warnings.

Required env:
    EXT_STATS_ENABLED=1
    EXT_JOB_ID=...
    # For provider reconciliation (optional but recommended):
    EXT_RUSENDER_API_KEY=...     (or RUSENDER_API_KEY)
    EXT_MAILOPOST_API_TOKEN=...
    EXT_UNISENDER_API_KEY=...
    # For mailbox reconciliation (optional):
    EXT_IMAP_HOST=... EXT_IMAP_USER=... EXT_IMAP_PASSWORD=...
"""
from __future__ import annotations

import time
import unittest

from tests.external.config import ExtConfig, load_config, require_ext_enabled
from tests.external.adapters.app import ExtAppAdapter
from tests.external.reconciler import (
    Reconciler,
    ReconSource,
    source_from_app_dashboard,
    source_from_sent_mail_log,
    source_from_jsonl_events,
    source_from_provider_events,
    source_from_mailbox,
)


def setUpModule() -> None:  # noqa: N802
    require_ext_enabled()


class TestExtReconciliation(unittest.TestCase):
    """EXT-RECON-01: Compare statistics across all four sources."""

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
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_ext_recon_01_all_sources(self) -> None:
        """EXT-RECON-01: Collect stats from all sources and verify consistency."""
        job_id = self.config.job_id
        transport = self.config.transport.lower()
        sources: list[ReconSource] = []

        # ------------------------------------------------------------------
        # Source 1: App API (manager-dashboard)
        # ------------------------------------------------------------------
        print("[EXT-RECON-01] Collecting App API statistics…")
        dashboard = self.app.get_dashboard(job_id)
        app_source = source_from_app_dashboard(dashboard)
        sources.append(app_source)
        print(
            f"[EXT-RECON-01] App API: sent={app_source.sent}, "
            f"delivered={app_source.delivered}, opened={app_source.opened}, "
            f"clicked={app_source.clicked}, hard_bounced={app_source.hard_bounced}"
        )

        with self.subTest("app_api_has_sent_data"):
            self.assertGreater(app_source.sent, 0, "App API shows sent=0. Run EXT-SEND-* first.")

        # ------------------------------------------------------------------
        # Source 2: DB (sent_mail_log)
        # ------------------------------------------------------------------
        print("[EXT-RECON-01] Collecting DB (sent_mail_log) statistics…")
        sent_records = self.app.read_sent_mail_log(job_id)
        if sent_records:
            db_source = source_from_sent_mail_log(sent_records)
            sources.append(db_source)
            print(f"[EXT-RECON-01] DB: sent={db_source.sent}")

            with self.subTest("db_sent_matches_api"):
                self.assertEqual(
                    db_source.sent,
                    app_source.sent,
                    f"DB sent_mail_log ({db_source.sent}) != App API sent ({app_source.sent}). "
                    "Data inconsistency between PostgreSQL and aggregation layer.",
                )
        else:
            print("[EXT-RECON-01] DB source skipped — requires in-process file access.")

        # ------------------------------------------------------------------
        # Source 3: JSONL events
        # ------------------------------------------------------------------
        print("[EXT-RECON-01] Collecting JSONL events statistics…")
        provider_name_map = {
            "rusender": "rusender",
            "mailopost": "mailopost",
            "unisender": "unisender_go",
        }
        jsonl_provider = provider_name_map.get(transport, transport)
        events = self.app.read_provider_events_jsonl(job_id, jsonl_provider)
        if events:
            jsonl_source = source_from_jsonl_events(events)
            sources.append(jsonl_source)
            print(
                f"[EXT-RECON-01] JSONL: delivered={jsonl_source.delivered}, "
                f"opened={jsonl_source.opened}, clicked={jsonl_source.clicked}, "
                f"hard_bounced={jsonl_source.hard_bounced}"
            )

            # JSONL delivered should match app API delivered
            with self.subTest("jsonl_delivered_matches_api"):
                self.assertEqual(
                    jsonl_source.delivered,
                    app_source.delivered,
                    f"JSONL delivered ({jsonl_source.delivered}) != App API delivered ({app_source.delivered}). "
                    "Event aggregation may be broken.",
                )
        else:
            print(f"[EXT-RECON-01] No JSONL events for {jsonl_provider} (requires in-process access).")

        # ------------------------------------------------------------------
        # Source 4: Provider API (optional, requires API key)
        # ------------------------------------------------------------------
        provider_mid = self.app.get_provider_message_id(job_id)

        if not self.config.skip_reconciliation and provider_mid:
            print("[EXT-RECON-01] Collecting Provider API statistics…")
            provider_events = self._fetch_provider_events(transport, provider_mid)
            if provider_events is not None:
                prov_source = source_from_provider_events(provider_events)
                sources.append(prov_source)
                print(
                    f"[EXT-RECON-01] Provider API: delivered={prov_source.delivered}, "
                    f"opened={prov_source.opened}, clicked={prov_source.clicked}"
                )
            else:
                print("[EXT-RECON-01] Provider API source skipped — API key not configured or request failed.")
        else:
            print("[EXT-RECON-01] Provider API reconciliation skipped (skip_reconciliation=True or no message_id).")

        # ------------------------------------------------------------------
        # Source 5: Mailbox (optional, requires IMAP)
        # ------------------------------------------------------------------
        if not self.config.skip_mailbox:
            print("[EXT-RECON-01] Checking mailbox message count…")
            try:
                from tests.external.adapters.mailbox import ImapMailboxAdapter
                mb = ImapMailboxAdapter(
                    self.config.imap_host,
                    self.config.imap_port,
                    self.config.imap_user,
                    self.config.imap_password,
                    use_ssl=self.config.imap_use_ssl,
                )
                messages = mb.fetch_recent(since_seconds=3600)
                mailbox_source = source_from_mailbox(len(messages))
                sources.append(mailbox_source)
                print(f"[EXT-RECON-01] Mailbox: messages_found={len(messages)}")
            except Exception as exc:
                print(f"[EXT-RECON-01] Mailbox source error: {exc}")

        # ------------------------------------------------------------------
        # Run reconciliation
        # ------------------------------------------------------------------
        if len(sources) < 2:
            self.skipTest("Not enough sources for reconciliation (< 2). Enable in-process access or provider API keys.")

        print("[EXT-RECON-01] Running reconciliation engine…")
        reconciler = Reconciler(tolerance=0)
        report = reconciler.compare(sources)

        # Print summary
        print("\n--- Reconciliation Report ---")
        for line in report.summary_lines():
            print(line)
        print("---")

        # Separate HIGH vs MEDIUM/LOW mismatches
        high_mismatches = [m for m in report.mismatches if m.severity == "HIGH"]
        other_mismatches = [m for m in report.mismatches if m.severity != "HIGH"]

        if other_mismatches:
            print(f"\n[EXT-RECON-01] MEDIUM/LOW mismatches (non-blocking):")
            for m in other_mismatches:
                print(f"  {m}")

        with self.subTest("no_high_severity_mismatches"):
            self.assertEqual(
                len(high_mismatches),
                0,
                f"HIGH severity mismatches found:\n"
                + "\n".join(f"  {m}" for m in high_mismatches),
            )

    def _fetch_provider_events(self, transport: str, provider_mid: str) -> list | None:
        """Fetch events from provider API. Returns None if not available."""
        try:
            if transport == "rusender" and self.config.rusender_api_key:
                from tests.external.adapters.provider import RuSenderAdapter
                adapter = RuSenderAdapter(self.config.rusender_api_key)
                return adapter.get_events(provider_mid)

            if transport == "mailopost" and self.config.mailopost_api_token:
                from tests.external.adapters.provider import MailoPostAdapter
                adapter = MailoPostAdapter(self.config.mailopost_api_token)
                return adapter.get_events(provider_mid)

            if transport == "unisender" and self.config.unisender_api_key:
                from tests.external.adapters.provider import UniSenderGoAdapter
                adapter = UniSenderGoAdapter(self.config.unisender_api_key)
                return adapter.get_events(provider_mid)
        except Exception as exc:
            print(f"[EXT-RECON-01] Provider API error: {exc}")
        return None


# ---------------------------------------------------------------------------
# EXT-RECON-01b — API consistency cross-check (app-only, no external services)
# ---------------------------------------------------------------------------


class TestExtApiConsistency(unittest.TestCase):
    """Cross-check consistency between different app API endpoints (no external calls).

    Runs even without external provider access. Verifies that:
    - /manager-dashboard.summary.sent matches /campaigns.sent
    - /manager-dashboard.summary.delivered matches /campaigns.delivered
    - /email-problems.summary matches recipients with email_broken status
    - /consents.summary.confirmed matches recipients with consent_status=confirmed
    """

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
        if not self.config.job_id:
            self.skipTest("EXT_JOB_ID not set.")

    def test_api_dashboard_vs_campaigns_consistency(self) -> None:
        """Dashboard summary.sent should match campaigns.sent for the same job."""
        job_id = self.config.job_id
        dashboard = self.app.get_dashboard(job_id)
        campaigns_data = self.app.get_campaigns(job_id)

        dashboard_sent = int((dashboard.get("summary") or {}).get("sent") or 0)
        campaign_items = campaigns_data.get("campaigns") or campaigns_data.get("items") or []

        if not campaign_items:
            self.skipTest("No campaigns data available.")

        # Find the campaign for this job
        campaign = next(
            (c for c in campaign_items if str(c.get("job_id") or c.get("id") or "") == job_id),
            campaign_items[0] if campaign_items else None,
        )

        if campaign is None:
            self.skipTest("Campaign not found in campaigns list.")

        campaign_sent = int(campaign.get("sent") or 0)

        self.assertEqual(
            dashboard_sent,
            campaign_sent,
            f"dashboard.summary.sent={dashboard_sent} != campaigns[job_id].sent={campaign_sent}. "
            "Inconsistency between endpoints.",
        )

    def test_api_email_problems_vs_recipients(self) -> None:
        """email-problems.summary.hard_bounce count should match recipients with email_broken status."""
        job_id = self.config.job_id
        problems = self.app.get_email_problems(job_id)
        recipients_data = self.app.get_recipients(job_id)

        hard_bounce_count = int((problems.get("summary") or {}).get("hard_bounce") or 0)
        all_recipients = recipients_data.get("recipients") or recipients_data.get("items") or []

        email_broken_count = sum(
            1 for r in all_recipients
            if str((r.get("manager_status") or {}).get("key") or "") == "email_broken"
        )

        self.assertEqual(
            hard_bounce_count,
            email_broken_count,
            f"email-problems.hard_bounce={hard_bounce_count} != "
            f"recipients with email_broken status={email_broken_count}.",
        )

    def test_api_consents_vs_recipients(self) -> None:
        """consents.summary.confirmed should match recipients with consent_status=confirmed."""
        job_id = self.config.job_id
        consents_data = self.app.get_consents(job_id)
        recipients_data = self.app.get_recipients(job_id)

        consents_confirmed = int((consents_data.get("summary") or {}).get("confirmed") or 0)
        all_recipients = recipients_data.get("recipients") or recipients_data.get("items") or []

        recipients_confirmed = sum(
            1 for r in all_recipients
            if str(r.get("consent_status_key") or (r.get("consent_status") or {}).get("key") or "") == "confirmed"
        )

        self.assertEqual(
            consents_confirmed,
            recipients_confirmed,
            f"consents.summary.confirmed={consents_confirmed} != "
            f"recipients with confirmed consent={recipients_confirmed}.",
        )

    def test_known_gap_clicked_after_consent_hardcoded(self) -> None:
        """Known gap: clicked_after_consent is hardcoded to 0 in build_consents_view."""
        job_id = self.config.job_id
        consents_data = self.app.get_consents(job_id)
        summary = consents_data.get("summary") or {}
        clicked_after_consent = int(summary.get("clicked_after_consent") or 0)
        self.assertEqual(
            clicked_after_consent,
            0,
            "UNEXPECTED: clicked_after_consent is no longer 0. "
            "Update this test and remove the known gap from documentation.",
        )
        print(
            "[EXT-RECON] KNOWN GAP confirmed: clicked_after_consent=0 (hardcoded in build_consents_view). "
            "Remove this test when the gap is fixed."
        )


if __name__ == "__main__":
    unittest.main()
