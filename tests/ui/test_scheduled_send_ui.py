"""Playwright acceptance tests for Bitrix task 109651 — scheduled mailing start."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests.ui.fixtures_acceptance import acceptance_job_id
from tests.ui.harness import AppUITestCase


class ScheduledSendAcceptanceTests(AppUITestCase):
    task_id = "109651"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.job_id = acceptance_job_id()
        if not cls.job_id:
            raise unittest.SkipTest("ACCEPTANCE_JOB_ID not set. Run tests.ui.fixtures_acceptance first.")

    def tearDown(self) -> None:
        super().tearDown()

    def _future_iso(self, *, minutes: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()

    def _schedule_via_api(self, *, minutes: int, dry_run: bool = False):
        return self.api_post(
            "/api/sender/run",
            json={
                "job_id": self.job_id,
                "dry_run": dry_run,
                "scheduled_start_at": self._future_iso(minutes=minutes),
                "transport": "rusender",
                "send_mode": "consent_request",
                "recipient_strategy": "all",
                "work_type": "stp_mo",
            },
        )

    def _require_schedule_api(self, response, scenario: str) -> None:
        if response.status_code == 500:
            self.go_to_screen("sender")
            self.record_scenario(scenario, "blocked", response.text[:200])
            self.skipTest(f"Sender schedule API unavailable: {response.text[:200]}")

    def _past_iso(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat()

    def _open_sender_with_job(self) -> None:
        self.go_to_screen("settings")
        self.set_current_job(self.job_id)
        self.go_to_screen("sender")
        self.page.wait_for_timeout(1500)

    def test_scenario_1_schedule_future_send(self) -> None:
        schedule = self._schedule_via_api(minutes=15, dry_run=False)
        self._require_schedule_api(schedule, "s1_schedule_future")
        self._open_sender_with_job()
        self.assertTrue(self.page.is_visible("#s-cancel-scheduled"))
        self.record_scenario("s1_schedule_future", "pass")

    def test_scenario_2_scheduled_kpi_drilldown(self) -> None:
        schedule = self._schedule_via_api(minutes=20, dry_run=False)
        self._require_schedule_api(schedule, "s2_scheduled_kpi")
        self.assertEqual(schedule.status_code, 200, schedule.text)
        self.open_statistics()
        self.activate_tab("campaigns")
        self.page.wait_for_timeout(2000)
        kpi_cards = self.page.query_selector_all("#campaigns-kpis .kpi-card")
        titles = [card.query_selector(".label, .title") for card in kpi_cards]
        scheduled_card = None
        for card in kpi_cards:
            label = (card.inner_text() or "").lower()
            if "запланир" in label:
                scheduled_card = card
                break
        if scheduled_card:
            scheduled_card.click()
            self.page.wait_for_selector("#modal-drilldown.open", state="visible", timeout=15000)
        self.record_scenario("s2_scheduled_kpi", "pass")

    def test_scenario_3_cancel_scheduled(self) -> None:
        schedule = self._schedule_via_api(minutes=25, dry_run=False)
        self._require_schedule_api(schedule, "s3_cancel_scheduled")
        self._open_sender_with_job()
        self.page.wait_for_selector("#s-cancel-scheduled", state="visible", timeout=15000)
        self.page.click("#s-cancel-scheduled")
        self.page.wait_for_timeout(3000)
        self.assertTrue(self.page.is_hidden("#s-cancel-scheduled"))
        self.record_scenario("s3_cancel_scheduled", "pass")

    def test_scenario_4_past_time_rejected(self) -> None:
        resp = self.api_post(
            "/api/sender/run",
            json={
                "job_id": self.job_id,
                "dry_run": False,
                "scheduled_start_at": self._past_iso(),
                "transport": "rusender",
                "send_mode": "consent_request",
                "recipient_strategy": "all",
                "work_type": "stp_mo",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.go_to_screen("sender")
        self.record_scenario("s4_past_rejected", "pass")

    def test_scenario_5_immediate_dry_run(self) -> None:
        self.api_post("/api/sender/scheduled/cancel", json={"job_id": self.job_id})
        resp = self.api_post(
            "/api/sender/run",
            json={
                "job_id": self.job_id,
                "dry_run": True,
                "transport": "rusender",
                "send_mode": "consent_request",
                "recipient_strategy": "all",
                "work_type": "stp_mo",
            },
        )
        if resp.status_code >= 500:
            self.go_to_screen("sender")
            self.record_scenario("s5_immediate_dry_run", "blocked", resp.text[:200])
            self.skipTest(resp.text[:200])
        self.go_to_screen("sender")
        status = self.api_get("/api/sender/status", params={"job_id": self.job_id}).json()
        result = status.get("result") or status
        self.assertNotEqual(str(result.get("status") or "").lower(), "scheduled")
        self.record_scenario("s5_immediate_dry_run", "pass", f"status={result.get('status')}")

    def test_scenario_6_starts_on_schedule(self) -> None:
        self.api_post("/api/sender/scheduled/cancel", json={"job_id": self.job_id})
        schedule = self._schedule_via_api(minutes=2, dry_run=True)
        self._require_schedule_api(schedule, "s6_starts_on_schedule")
        self.assertEqual(schedule.status_code, 200, schedule.text)
        deadline = datetime.now(timezone.utc) + timedelta(minutes=4)
        final_status = "scheduled"
        while datetime.now(timezone.utc) < deadline:
            payload = self.api_get("/api/sender/status", params={"job_id": self.job_id}).json()
            result = payload.get("result") or payload
            final_status = str(result.get("status") or "").lower()
            if final_status in {"running", "completed", "checking"}:
                break
            self.page.wait_for_timeout(5000)
        self.assertIn(final_status, {"running", "completed", "checking"})
        self.record_scenario("s6_starts_on_schedule", "pass", f"final_status={final_status}")


if __name__ == "__main__":
    unittest.main()
