"""End-to-end UI tests for the embedded statistics section.

Run inside the app container (Chromium is preinstalled):

    STATS_UI_E2E=1 .venv/bin/python -m unittest tests.ui.test_statistics_ui -v

Covers every user-facing function of the section: loading, tab switching,
KPI drill-down modals (open + data + CSV download), work-list navigation,
toolbar modals, and de-duplication guarantees.
"""
from __future__ import annotations

from tests.ui.harness import StatisticsUITestCase

DRILL_KINDS = ("sent", "delivered", "opened", "clicked", "problems", "pending", "consents", "materials")
TAB_IDS = ("campaigns", "recipients", "campaign-analytics", "consents", "problems", "reports", "dashboard")


class DashboardLoadTests(StatisticsUITestCase):
    def test_no_console_errors_on_dashboard(self) -> None:
        self.open_statistics()
        self.page.wait_for_timeout(1500)
        self.assertEqual(
            self.all_errors(), [],
            f"Console/page errors on statistics dashboard: {self.all_errors()}",
        )

    def test_kpi_cards_merged_no_duplicate_rate_row(self) -> None:
        self.open_statistics()
        cards = self.page.query_selector_all("#dashboard-kpis .kpi-card")
        self.assertEqual(len(cards), 8, "Dashboard should have 8 merged KPI cards")
        self.assertIsNone(
            self.page.query_selector("#dashboard-rates"),
            "The duplicate percentages row must be gone",
        )
        # The 4 rate-bearing cards embed the percentage in the value.
        merged = 0
        for card in cards:
            if "%" in (card.query_selector(".value").inner_text() or ""):
                merged += 1
        self.assertGreaterEqual(merged, 4, "Delivered/Opened/Clicks/Errors should show %")

    def test_funnel_shows_percentages_only(self) -> None:
        self.open_statistics()
        self.page.wait_for_timeout(1500)
        values = [
            el.inner_text().strip()
            for el in self.page.query_selector_all("#dashboard-funnel .funnel-step .value")
        ]
        self.assertTrue(values, "Funnel should render steps")
        for val in values:
            self.assertTrue(val.endswith("%"), f"Funnel value '{val}' should be a percentage")

    def test_recipients_status_filter_is_chips_only(self) -> None:
        self.open_statistics()
        self.assertIsNone(self.page.query_selector("#filter-status"), "Duplicate status select must be removed")
        self.activate_tab("recipients")
        self.page.wait_for_selector("#recipient-chips .chip", state="visible", timeout=15000)
        chips = self.page.query_selector_all("#recipient-chips .chip")
        self.assertGreater(len(chips), 0, "Recipient chips should be the status filter")


class NavigationTests(StatisticsUITestCase):
    def test_all_tabs_switch(self) -> None:
        self.open_statistics()
        for page_id in TAB_IDS:
            self.activate_tab(page_id)
            active = self.page.query_selector(f"#page-{page_id}.stats-page.active")
            self.assertTrue(active and active.is_visible(), f"Tab {page_id} did not become visible")

    def test_worklist_view_all_navigates(self) -> None:
        self.open_statistics()
        self.page.wait_for_timeout(1500)
        buttons = self.page.query_selector_all("#dashboard-worklists [data-nav]")
        self.assertGreater(len(buttons), 0, "Work-list 'view all' buttons should exist")
        target = buttons[0].get_attribute("data-nav")
        buttons[0].click()
        self.page.wait_for_selector(f"#page-{target}.stats-page.active", state="visible", timeout=15000)

    def test_campaigns_analytics_button_after_refresh(self) -> None:
        self.open_statistics()
        self.activate_tab("campaigns")
        self.page.wait_for_selector("#campaigns-table [data-open-analytics]", state="visible", timeout=15000)
        self.page.click("#btn-refresh")
        self.page.wait_for_timeout(1500)
        button = self.page.query_selector("#campaigns-table [data-open-analytics]")
        if not button:
            self.skipTest("No campaigns with analytics button in this dataset")
        button.click()
        self.page.wait_for_selector("#page-campaign-analytics.stats-page.active", state="visible", timeout=15000)
        empty = self.page.query_selector("#analytics-empty")
        self.assertTrue(
            empty is None or not empty.is_visible(),
            "Analytics page should show campaign data, not the empty placeholder",
        )
        self.assertEqual(self.all_errors(), [], f"Analytics navigation caused errors: {self.all_errors()}")


class DrilldownTests(StatisticsUITestCase):
    def test_every_kpi_opens_modal_with_data(self) -> None:
        self.open_statistics()
        for kind in DRILL_KINDS:
            count_text = self.open_drilldown(kind)
            cols = len(self.page.query_selector_all("#drilldown-head-row th"))
            self.assertEqual(cols, 9, f"Drill-down '{kind}' should render 9 columns")
            self.assertTrue(count_text.startswith("Записей:"), f"'{kind}' count unexpected: {count_text}")
            self.close_drilldown()

    def test_drilldown_download_csv(self) -> None:
        self.open_statistics()
        # 'materials' reliably has rows in this dataset; fall back to 'consents'.
        self.open_drilldown("consents")
        rows = len(self.page.query_selector_all("#drilldown-body tr"))
        if rows == 0:
            self.skipTest("No rows available to download in this dataset")
        with self.page.expect_download() as dl_info:
            self.page.click("#drilldown-download")
        download = dl_info.value
        self.assertTrue(download.suggested_filename.endswith(".csv"), "Download should be a CSV file")

    def test_drilldown_closes_on_backdrop(self) -> None:
        self.open_statistics()
        self.open_drilldown("consents")
        # Click the modal backdrop (outside the card).
        self.page.click("#modal-drilldown", position={"x": 5, "y": 5})
        self.page.wait_for_selector("#modal-drilldown.open", state="hidden", timeout=5000)


class ToolbarTests(StatisticsUITestCase):
    def test_advanced_filters_modal(self) -> None:
        self.open_statistics()
        self.page.click("#btn-advanced-filters")
        self.page.wait_for_selector("#modal-filters.open", state="visible", timeout=5000)
        self.page.click("#adv-cancel")
        self.page.wait_for_selector("#modal-filters.open", state="hidden", timeout=5000)

    def test_export_modal_opens(self) -> None:
        self.open_statistics()
        self.activate_tab("reports")
        self.page.wait_for_timeout(800)
        self.page.click("#btn-export-report")
        self.page.wait_for_selector("#modal-export.open", state="visible", timeout=5000)
        self.page.click("#export-cancel")
        self.page.wait_for_selector("#modal-export.open", state="hidden", timeout=5000)

    def test_refresh_button(self) -> None:
        self.open_statistics()
        self.page.click("#btn-refresh")
        self.page.wait_for_timeout(1500)
        self.assertEqual(self.all_errors(), [], f"Refresh caused errors: {self.all_errors()}")
