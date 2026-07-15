"""Playwright acceptance tests for Bitrix task 109652 — auto-call contact export."""
from __future__ import annotations

import unittest

from tests.ui.fixtures_acceptance import acceptance_job_id
from tests.ui.harness import AppUITestCase


class AutoCallExportAcceptanceTests(AppUITestCase):
    task_id = "109652"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.job_id = acceptance_job_id()
        if not cls.job_id:
            raise unittest.SkipTest("ACCEPTANCE_JOB_ID not set. Run tests.ui.fixtures_acceptance first.")

    def tearDown(self) -> None:
        super().tearDown()

    def test_scenario_1_analytics_quick_export(self) -> None:
        self.select_job_in_statistics(self.job_id)
        self.activate_tab("campaign-analytics")
        phones = self.download_csv(lambda: self.page.click("#btn-export-auto-call"))
        self.assertEqual(phones, ["73439396979", "79156848204"])
        self.record_scenario("s1_analytics_export", "pass", f"phones={phones}")

    def test_scenario_2_export_modal_csv_only(self) -> None:
        self.open_statistics()
        self.activate_tab("reports")
        self.page.click("#btn-export-report")
        self.page.wait_for_selector("#modal-export.open", state="visible", timeout=10000)
        self.page.select_option("#export-type", "auto_call_contacts")
        self.page.wait_for_timeout(500)
        disabled = self.page.eval_on_selector_all(
            "#export-format option",
            "opts => opts.filter(o => o.disabled).map(o => o.value)",
        )
        self.assertEqual(set(disabled), {"xlsx", "ndjson"})
        phones = self.export_auto_call_via_modal(self.job_id)
        self.assertEqual(phones, ["73439396979", "79156848204"])
        self.record_scenario("s2_export_modal", "pass", f"phones={phones}")

    def test_scenario_3_api_endpoint(self) -> None:
        ok = self.api_get(f"/api/download/auto-call-contacts?job_id={self.job_id}")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("phone_number", ok.text.splitlines()[0])
        bad = self.api_get("/api/download/auto-call-contacts")
        self.assertEqual(bad.status_code, 400)
        self.go_to_screen("statistics")
        self.record_scenario("s3_api_endpoint", "pass", f"200 ok, 400 without job_id")

    def test_scenarios_4_6_csv_normalization_dedup_invalid(self) -> None:
        resp = self.api_get(f"/api/download/auto-call-contacts?job_id={self.job_id}")
        lines = resp.text.splitlines()
        self.assertEqual(lines[0].lstrip("\ufeff"), "phone_number")
        phones = lines[1:]
        self.assertIn("73439396979", phones)
        self.assertIn("79156848204", phones)
        self.assertEqual(len(phones), len(set(phones)))
        for phone in phones:
            self.assertRegex(phone, r"^7\d{10}$")
        self.assertNotIn("invalid", phones)
        self.assertNotIn("123", phones)
        self.record_scenario("s4_6_normalization", "pass", f"phones={phones}")

    def test_scenario_7_utf8_bom_single_column(self) -> None:
        resp = self.api_get(f"/api/download/auto-call-contacts?job_id={self.job_id}")
        raw = resp.content
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "CSV must have UTF-8 BOM")
        for line in resp.text.splitlines()[1:]:
            self.assertNotRegex(line, r"[+\s()]")
        self.record_scenario("s7_encoding", "pass")

    def test_scenario_8_campaign_row_export(self) -> None:
        self.open_statistics()
        self.activate_tab("campaigns")
        self.page.wait_for_timeout(2000)
        row = self.page.query_selector(f'#campaigns-table tr[data-job-id="{self.job_id}"]')
        if row is None:
            self.record_scenario("s8_campaign_row", "blocked", "job not listed in campaigns (no sent_mail_log yet)")
            self.skipTest("Acceptance job is not visible in campaigns list yet")
        row.click()
        self.page.wait_for_selector("#modal-campaign-summary.open", state="visible", timeout=10000)
        phones = self.download_csv(lambda: self.page.click("#campaign-export-auto-call"))
        self.assertEqual(phones, ["73439396979", "79156848204"])
        self.record_scenario("s8_campaign_row", "pass", f"phones={phones}")


if __name__ == "__main__":
    unittest.main()
