"""Playwright E2E harness for the embedded statistics section.

These tests drive the real UI in a headless Chromium (already installed in the
app image via ``playwright install chromium``). They are gated behind
``STATS_UI_E2E=1`` or ``ACCEPTANCE_UI_E2E=1`` so they never run in environments
without a browser.

Auth reuses the app's session cookie: ``POST /api/auth/login`` returns the
``mailing_agent_session`` cookie which we inject into the browser context.
"""
from __future__ import annotations

import csv
import io
import os
import unittest
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.environ.get(
    "STATS_UI_BASE_URL", os.environ.get("E2E_BASE_URL", "http://localhost:9806")
).rstrip("/")
USERNAME = os.environ.get("E2E_USERNAME", os.environ.get("APP_USERNAME", "admin"))
PASSWORD = os.environ.get("E2E_PASSWORD", os.environ.get("APP_PASSWORD", "change-me"))
SESSION_COOKIE_NAME = "mailing_agent_session"
STATS_ENABLED = os.environ.get("STATS_UI_E2E", "").strip() == "1"
ACCEPTANCE_ENABLED = os.environ.get("ACCEPTANCE_UI_E2E", "").strip() == "1"
ENABLED = STATS_ENABLED or ACCEPTANCE_ENABLED
ARTIFACTS_ROOT = Path(os.environ.get("ACCEPTANCE_ARTIFACTS_DIR", "tmp/acceptance"))

# Console message types that indicate a real problem in the page.
FATAL_CONSOLE_TYPES = {"error"}


def require_enabled(*, acceptance: bool = False) -> None:
    if acceptance and not ACCEPTANCE_ENABLED:
        raise unittest.SkipTest(
            "Acceptance UI E2E tests are disabled. Set ACCEPTANCE_UI_E2E=1 to run them "
            "(requires a reachable app at E2E_BASE_URL and Chromium)."
        )
    if not acceptance and not STATS_ENABLED:
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


class _PlaywrightTestCase(unittest.TestCase):
    """Shared Playwright session: login cookie, browser, console error capture."""

    playwright: Any = None
    browser: Any = None
    task_id: str = "shared"

    @classmethod
    def setUpClass(cls) -> None:
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


class StatisticsUITestCase(_PlaywrightTestCase):
    """Base class: logs in, opens the statistics section, records console errors."""

    @classmethod
    def setUpClass(cls) -> None:
        require_enabled(acceptance=False)
        super().setUpClass()


class AppUITestCase(_PlaywrightTestCase):
    """Acceptance tests: navigate any screen, capture screenshots, call API."""

    task_id = "000000"
    _class_scenario_results: dict[str, list[dict[str, str]]] = {}

    @classmethod
    def setUpClass(cls) -> None:
        require_enabled(acceptance=True)
        super().setUpClass()
        cls._class_scenario_results.setdefault(cls.task_id, [])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._write_class_results()
        super().tearDownClass()

    @classmethod
    def _write_class_results(cls) -> None:
        results = cls._class_scenario_results.get(cls.task_id, [])
        if not results:
            return
        results_path = ARTIFACTS_ROOT / cls.task_id / "results.json"
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    def setUp(self) -> None:
        super().setUp()
        self._scenario_results: list[dict[str, str]] = []

    def go_to_screen(self, screen_id: str) -> None:
        self.page.goto(f"{BASE_URL}/", wait_until="networkidle")
        self.page.wait_for_timeout(1500)
        self.page.evaluate(
            """(screenId) => {
              if (typeof activateScreen === 'function') {
                activateScreen(screenId, { persist: false, scroll: false });
              } else if (typeof goToScreen === 'function') {
                goToScreen(screenId);
              }
            }""",
            screen_id,
        )
        self.page.wait_for_selector(f"#s-{screen_id}.active", state="visible", timeout=15000)

    def screenshot(self, scenario: str, *, full_page: bool = True) -> Path:
        task_dir = ARTIFACTS_ROOT / self.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        safe_name = scenario.replace(" ", "_").replace("/", "-")
        path = task_dir / f"{safe_name}.png"
        self.page.screenshot(path=str(path), full_page=full_page)
        return path

    def record_scenario(self, scenario: str, status: str, detail: str = "") -> None:
        entry = {"scenario": scenario, "status": status, "detail": detail}
        self._scenario_results.append(entry)
        self._class_scenario_results.setdefault(self.task_id, []).append(entry)
        self.screenshot(scenario)
        self._write_class_results()

    def api_client(self) -> httpx.Client:
        client = httpx.Client(base_url=BASE_URL, timeout=60.0, follow_redirects=True)
        client.cookies.set(SESSION_COOKIE_NAME, self._token)
        return client

    def api_get(self, path: str, **kwargs: Any) -> httpx.Response:
        with self.api_client() as client:
            return client.get(path, **kwargs)

    def api_post(self, path: str, **kwargs: Any) -> httpx.Response:
        with self.api_client() as client:
            return client.post(path, **kwargs)

    def api_patch(self, path: str, **kwargs: Any) -> httpx.Response:
        with self.api_client() as client:
            return client.patch(path, **kwargs)

    def api_delete(self, path: str, **kwargs: Any) -> httpx.Response:
        with self.api_client() as client:
            return client.delete(path, **kwargs)

    def set_current_job(self, job_id: str) -> None:
        self.page.evaluate("(jobId) => { if (typeof setCurrentJobId === 'function') setCurrentJobId(jobId); }", job_id)

    def download_csv(self, trigger: Any) -> list[str]:
        with self.page.expect_download(timeout=30000) as download_info:
            trigger()
        download = download_info.value
        temp_path = Path(download.path())
        content = temp_path.read_text(encoding="utf-8-sig")
        rows = list(csv.reader(io.StringIO(content)))
        self.assertTrue(rows, "CSV should not be empty")
        return [row[0] for row in rows[1:] if row]

    def download_csv_via_navigation(self, url: str) -> list[str]:
        with self.page.expect_download(timeout=30000) as download_info:
            try:
                self.page.goto(url)
            except Exception as exc:
                if "Download is starting" not in str(exc):
                    raise
        download = download_info.value
        temp_path = Path(download.path())
        content = temp_path.read_text(encoding="utf-8-sig")
        rows = list(csv.reader(io.StringIO(content)))
        self.assertTrue(rows, "CSV should not be empty")
        return [row[0] for row in rows[1:] if row]

    def export_auto_call_via_modal(self, job_id: str) -> list[str]:
        self.page.evaluate(
            """(jobId) => {
              const select = document.getElementById('export-campaign');
              if (!select) return;
              let option = Array.from(select.options).find((item) => item.value === jobId);
              if (!option) {
                option = document.createElement('option');
                option.value = jobId;
                option.textContent = jobId;
                select.appendChild(option);
              }
              select.value = jobId;
            }""",
            job_id,
        )
        with self.page.expect_response(
            lambda response: "/api/sender/reports/export" in response.url and response.status == 200,
            timeout=45000,
        ) as response_info:
            self.page.click("#export-submit")
        payload = response_info.value.json()
        report_id = (payload.get("result") or payload).get("report_id")
        self.assertTrue(report_id, payload)
        return self.download_csv_via_navigation(f"{BASE_URL}/api/sender/reports/download/{report_id}")

    def select_job_in_statistics(self, job_id: str) -> None:
        self.open_statistics()
        self.page.evaluate(
            """(jobId) => {
              const select = document.getElementById('analytics-campaign');
              if (!select) return;
              const option = document.createElement('option');
              option.value = jobId;
              option.textContent = jobId;
              select.appendChild(option);
              select.value = jobId;
              select.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            job_id,
        )
        self.page.wait_for_timeout(1000)

    def open_statistics(self) -> None:
        self.go_to_screen("statistics")
        self.page.wait_for_selector("#page-dashboard.stats-page.active", state="visible", timeout=15000)
        self.page.wait_for_timeout(1500)

    def activate_tab(self, page_id: str) -> None:
        self.page.click(f'.stx-tab[data-page="{page_id}"]')
        self.page.wait_for_selector(f"#page-{page_id}.stats-page.active", state="visible", timeout=15000)

    def open_drilldown(self, kind: str, timeout_ms: int = 45000) -> str:
        self.page.click(f'#dashboard-kpis .kpi-card[data-drill="{kind}"]')
        self.page.wait_for_selector("#modal-drilldown.open", state="visible", timeout=10000)
        self.page.wait_for_function(
            "() => { const el = document.getElementById('drilldown-count');"
            " return el && el.textContent && el.textContent.trim() !== 'Загрузка…'; }",
            timeout=timeout_ms,
        )
        count_text = self.page.query_selector("#drilldown-count").inner_text()
        if "Не удалось" in count_text:
            raise AssertionError(f"Drill-down '{kind}' failed to load: {count_text}")
        return count_text
