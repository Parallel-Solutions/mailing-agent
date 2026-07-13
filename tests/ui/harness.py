"""Playwright E2E harness for the embedded statistics section.

These tests drive the real UI in a headless Chromium (already installed in the
app image via ``playwright install chromium``). They are gated behind
``STATS_UI_E2E=1`` so they never run in environments without a browser.

Auth reuses the app's session cookie: ``POST /api/auth/login`` returns the
``mailing_agent_session`` cookie which we inject into the browser context.
"""
from __future__ import annotations

import os
import unittest
from typing import Any

import httpx

BASE_URL = os.environ.get(
    "STATS_UI_BASE_URL", os.environ.get("E2E_BASE_URL", "http://localhost:9806")
).rstrip("/")
USERNAME = os.environ.get("E2E_USERNAME", os.environ.get("APP_USERNAME", "admin"))
PASSWORD = os.environ.get("E2E_PASSWORD", os.environ.get("APP_PASSWORD", "change-me"))
SESSION_COOKIE_NAME = "mailing_agent_session"
ENABLED = os.environ.get("STATS_UI_E2E", "").strip() == "1"

# Console message types that indicate a real problem in the page.
FATAL_CONSOLE_TYPES = {"error"}


def require_enabled() -> None:
    if not ENABLED:
        raise unittest.SkipTest(
            "Statistics UI E2E tests are disabled. Set STATS_UI_E2E=1 to run them "
            "(requires a reachable app at STATS_UI_BASE_URL and Chromium)."
        )


def _login_cookie() -> str:
    try:
        with httpx.Client(base_url=BASE_URL, timeout=30.0, follow_redirects=True) as client:
            resp = client.post(
                "/api/auth/login",
                json={"username": USERNAME, "password": PASSWORD},
            )
            if resp.status_code >= 400:
                raise unittest.SkipTest(
                    f"Statistics UI E2E: login failed ({resp.status_code}). "
                    "Configure E2E_USERNAME/E2E_PASSWORD."
                )
            token = resp.cookies.get(SESSION_COOKIE_NAME)
            if not token:
                raise unittest.SkipTest("Statistics UI E2E: login returned no session cookie.")
            return token
    except (httpx.HTTPError, OSError) as exc:
        raise unittest.SkipTest(
            f"Statistics UI E2E: app unreachable at {BASE_URL} ({exc})."
        ) from exc


class StatisticsUITestCase(unittest.TestCase):
    """Base class: logs in, opens the statistics section, records console errors."""

    playwright: Any = None
    browser: Any = None

    @classmethod
    def setUpClass(cls) -> None:
        require_enabled()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on image
            raise unittest.SkipTest(f"Playwright not available: {exc}") from exc
        cls._token = _login_cookie()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - depends on image
            cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium launch failed: {exc}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.browser is not None:
            cls.browser.close()
        if cls.playwright is not None:
            cls.playwright.stop()

    def setUp(self) -> None:
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.context = self.browser.new_context(accept_downloads=True)
        self.context.add_cookies(
            [{"name": SESSION_COOKIE_NAME, "value": self._token, "url": BASE_URL}]
        )
        self.page = self.context.new_page()
        self.page.on("console", self._on_console)
        self.page.on("pageerror", lambda err: self.page_errors.append(str(err)))

    def tearDown(self) -> None:
        self.context.close()

    def _on_console(self, msg: Any) -> None:
        if msg.type in FATAL_CONSOLE_TYPES:
            self.console_errors.append(msg.text)

    # -- helpers -------------------------------------------------------------

    def open_statistics(self) -> None:
        """Load the app shell and open the embedded statistics section.

        The host shell restores the last active screen asynchronously (server UI
        state), which can race a raw nav click. We wait for the network to settle
        first, then switch screens via the host's own ``goToScreen`` and confirm
        the section is actually visible before interacting.
        """
        self.page.goto(f"{BASE_URL}/", wait_until="networkidle")
        self.page.wait_for_selector("#s-statistics", state="attached", timeout=15000)
        # Let the async UI-state restore finish so it does not switch us away.
        self.page.wait_for_timeout(1500)
        self.page.evaluate("() => window.goToScreen && window.goToScreen('statistics')")
        self.page.wait_for_selector("#s-statistics.active", state="visible", timeout=15000)
        self.page.wait_for_selector("#page-dashboard.stats-page.active", state="visible", timeout=15000)
        # Dashboard data loads asynchronously; wait for real KPI values.
        self.page.wait_for_timeout(1500)

    def all_errors(self) -> list[str]:
        return self.console_errors + self.page_errors

    def activate_tab(self, page_id: str) -> None:
        self.page.click(f'.stx-tab[data-page="{page_id}"]')
        self.page.wait_for_selector(f"#page-{page_id}.stats-page.active", state="visible", timeout=15000)

    def open_drilldown(self, kind: str, timeout_ms: int = 45000) -> str:
        """Click a KPI tile and wait until its table finished loading.

        Returns the text of the count label. Raises AssertionError if the modal
        reports a load error.
        """
        self.page.click(f'#dashboard-kpis .kpi-card[data-drill="{kind}"]')
        self.page.wait_for_selector("#modal-drilldown.open", state="visible", timeout=10000)
        # Wait for loading to finish (count text stops being the spinner text).
        self.page.wait_for_function(
            "() => { const el = document.getElementById('drilldown-count');"
            " return el && el.textContent && el.textContent.trim() !== 'Загрузка…'; }",
            timeout=timeout_ms,
        )
        count_text = self.page.query_selector("#drilldown-count").inner_text()
        if "Не удалось" in count_text:
            raise AssertionError(f"Drill-down '{kind}' failed to load: {count_text}")
        return count_text

    def close_drilldown(self) -> None:
        self.page.click("#drilldown-close")
        self.page.wait_for_selector("#modal-drilldown.open", state="hidden", timeout=5000)
