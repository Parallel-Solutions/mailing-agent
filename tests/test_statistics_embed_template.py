from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "templates" / "index.html"
STATISTICS_JS = REPO_ROOT / "src" / "web" / "static" / "statistics.js"
STATISTICS_CSS = REPO_ROOT / "src" / "web" / "static" / "statistics.css"
MAIN_PY = REPO_ROOT / "main.py"


class StatisticsEmbedTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = INDEX_HTML.read_text(encoding="utf-8")
        self.stats_js = STATISTICS_JS.read_text(encoding="utf-8")
        self.stats_css = STATISTICS_CSS.read_text(encoding="utf-8")

    def test_statistics_screen_embedded_in_index(self) -> None:
        self.assertIn('id="s-statistics"', self.index)
        self.assertIn('class="stats-embed"', self.index)
        for page in (
            "dashboard",
            "campaigns",
            "recipients",
            "campaign-analytics",
            "consents",
            "problems",
            "reports",
        ):
            self.assertIn(f'id="page-{page}"', self.index)

    def test_sidebar_and_tabs_wired(self) -> None:
        self.assertIn('data-nav-screen="statistics"', self.index)
        self.assertIn('class="stx-tab', self.index)
        self.assertNotIn('Открыть раздел статистики для менеджера', self.index)

    def test_static_assets_included(self) -> None:
        self.assertIn('href="/public/statistics.css"', self.index)
        self.assertIn('/public/statistics.js', self.index)
        self.assertIn('/public/chart.min.js', self.index)

    def test_no_navigation_away_to_statistics_page(self) -> None:
        # The old standalone link must be gone; the section lives inside the SPA.
        self.assertNotIn('href="/statistics"', self.index)

    def test_standalone_statistics_template_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "templates" / "statistics.html").exists())

    def test_statistics_js_is_lazy_and_url_free(self) -> None:
        self.assertIn("window.StatsEmbed", self.stats_js)
        self.assertNotIn("history.replaceState", self.stats_js)
        self.assertNotIn("window.location.search", self.stats_js)
        self.assertIn(".stx-tab", self.stats_js)
        self.assertIn(".stx-modal", self.stats_js)
        self.assertIn("analytics-campaign", self.stats_js)
        self.assertIn("readGlobalFiltersFromDom", self.stats_js)

    def test_statistics_js_uses_session_storage_stale_while_revalidate(self) -> None:
        self.assertIn("sessionStorage", self.stats_js)
        self.assertIn("readDashboardCache", self.stats_js)
        self.assertIn("writeDashboardCache", self.stats_js)
        self.assertIn("dashboardCacheKey", self.stats_js)

    def test_recipient_status_filter_lives_on_recipients_page(self) -> None:
        recipients_section = self.index.split('id="page-recipients"', 1)[1].split('</section>', 1)[0]
        self.assertIn('id="filter-status"', recipients_section)
        header_bar = self.index.split('class="filters-bar"', 1)[1].split('</div>', 1)[0]
        self.assertNotIn('id="filter-status"', header_bar)

    def test_statistics_css_is_scoped(self) -> None:
        self.assertIn("#s-statistics", self.stats_css)
        # No unscoped app-shell layout leaking into the host page.
        self.assertNotIn(".app-shell", self.stats_css)

    def test_statistics_route_redirects(self) -> None:
        main_src = MAIN_PY.read_text(encoding="utf-8")
        self.assertNotIn('"statistics.html"', main_src)


if __name__ == "__main__":
    unittest.main()
