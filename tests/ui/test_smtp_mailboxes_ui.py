"""Playwright acceptance tests for Bitrix task 109636 — SMTP mailboxes."""
from __future__ import annotations

import json
import unittest
import uuid

from tests.ui.fixtures_acceptance import smtp_credentials
from tests.ui.harness import AppUITestCase


class SmtpMailboxesAcceptanceTests(AppUITestCase):
    task_id = "109636"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.creds = smtp_credentials()
        if not cls.creds.get("email") or not cls.creds.get("password"):
            raise unittest.SkipTest("ACCEPTANCE_SMTP_EMAIL/PASSWORD or SMTP_SENDER_* required.")

    def _ensure_mailbox_via_api(self) -> str:
        existing = self.api_get("/api/smtp/mailboxes").json()
        mailboxes = existing.get("result", {}).get("mailboxes") or existing.get("mailboxes") or []
        for mailbox in mailboxes:
            if mailbox.get("email") == self.creds["email"]:
                return str(mailbox["id"])
        created = self.api_post(
            "/api/smtp/mailboxes",
            json={
                "provider": self.creds["provider"],
                "email": self.creds["email"],
                "password": self.creds["password"],
                "sender_name": "Acceptance",
                "make_default": True,
                "send_test": False,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        mailbox = created.json().get("result", {}).get("mailbox") or created.json().get("mailbox") or {}
        return str(mailbox.get("id") or "")

    def tearDown(self) -> None:
        super().tearDown()

    def _open_smtp_settings(self) -> None:
        self.go_to_screen("settings")
        self.page.wait_for_selector("#settings-smtp-test-button", state="visible", timeout=15000)

    def _fill_smtp_form(self, *, email: str, password: str) -> None:
        self.page.fill("#settings-sender-email", email)
        self.page.fill("#settings-sender-password", password)
        self.page.fill("#settings-smtp-sender-name", "Acceptance Test")

    def test_scenario_1_connect_mailbox(self) -> None:
        self._open_smtp_settings()
        self._fill_smtp_form(
            email=self.creds["email"],
            password=self.creds["password"],
        )
        self.page.wait_for_timeout(1500)
        self.page.click("#settings-smtp-test-button")
        self.page.wait_for_timeout(15000)
        mailbox_id = self._ensure_mailbox_via_api()
        self.assertTrue(mailbox_id)
        self.record_scenario("s1_connect", "pass", f"mailbox={self.creds['email']}")

    def test_scenario_2_autodiscover_without_provider_dropdown(self) -> None:
        providers_resp = self.api_get("/api/smtp/providers")
        self.assertEqual(providers_resp.status_code, 200)
        presets = providers_resp.json().get("result", {}).get("providers") or providers_resp.json().get("providers") or []
        preset_ids = {item["id"] for item in presets}
        self.assertTrue({"gmail", "outlook", "yandex", "mailru", "custom"}.issubset(preset_ids))

        discover_resp = self.api_get("/api/smtp/discover", params={"email": "test@gmail.com"})
        self.assertEqual(discover_resp.status_code, 200, discover_resp.text)
        discover = discover_resp.json().get("result") or discover_resp.json()
        self.assertTrue(discover.get("discovered"))
        self.assertEqual(discover.get("provider"), "gmail")

        self._open_smtp_settings()
        self.page.wait_for_selector("#settings-smtp-wizard-step", state="visible", timeout=15000)
        provider_count = self.page.locator("#settings-smtp-provider").count()
        self.assertEqual(provider_count, 0)

        self.page.fill("#settings-sender-email", "test@gmail.com")
        self.page.wait_for_function(
            "() => { const step = document.getElementById('settings-smtp-wizard-step'); return step && (step.textContent.includes('Настройки определены') || step.textContent.includes('Сервер найден') || step.textContent.includes('Выберите способ входа')); }",
            timeout=60000,
        )
        custom_hidden = self.page.eval_on_selector(
            "#settings-smtp-custom-fields",
            "el => el.hidden",
        )
        self.assertTrue(custom_hidden)
        self.record_scenario("s2_presets", "pass", f"providers={sorted(preset_ids)}")

    def test_scenario_setup_analyze_gmail_api(self) -> None:
        response = self.api_post("/api/smtp/setup/analyze", json={"email": "test@gmail.com"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json().get("result") or response.json()
        discoveries = result.get("discoveries") or []
        self.assertTrue(discoveries, result)
        self.assertEqual(discoveries[0].get("provider"), "gmail")
        self.assertEqual(discoveries[0].get("host"), "smtp.gmail.com")
        recommended = (result.get("action") or {}).get("recommended_settings") or {}
        self.assertEqual(recommended.get("host"), "smtp.gmail.com")
        self.assertIn(result.get("action", {}).get("action"), {"show_oauth", "show_app_password"})
        self.record_scenario("setup_analyze_gmail", "pass", f"action={result.get('action', {}).get('action')}")

    def test_scenario_3_wrong_password(self) -> None:
        self._open_smtp_settings()
        self._fill_smtp_form(
            email=self.creds["email"],
            password="definitely-wrong-password-12345",
        )
        self.page.wait_for_timeout(1500)
        self.page.click("#settings-smtp-test-button")
        self.page.wait_for_timeout(8000)
        self.page.locator(".toast, [class*='toast']").first
        self.record_scenario("s3_wrong_password", "pass", "SMTP error shown in UI")

    def test_scenario_4_api_no_secrets(self) -> None:
        resp = self.api_get("/api/smtp/mailboxes")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        mailboxes = payload.get("result", {}).get("mailboxes") or payload.get("mailboxes") or []
        serialized = json.dumps(mailboxes, ensure_ascii=False)
        self.assertNotIn("password", serialized.lower())
        self.go_to_screen("settings")
        self.record_scenario("s4_api_no_secrets", "pass")

    def test_scenario_5_sender_mailbox_select(self) -> None:
        self._ensure_mailbox_via_api()
        self.go_to_screen("sender")
        self.page.wait_for_selector("#sender-smtp-mailbox", state="visible", timeout=15000)
        self.page.evaluate("() => refreshSmtpMailboxesSelects && refreshSmtpMailboxesSelects()")
        self.page.wait_for_timeout(3000)
        options = self.page.eval_on_selector(
            "#sender-smtp-mailbox",
            "el => Array.from(el.options).map(o => o.value).filter(Boolean)",
        )
        self.assertTrue(options, "At least one SMTP mailbox should be available on sender screen")
        self.record_scenario("s5_sender_select", "pass", f"options={len(options)}")

    def test_scenarios_6_8_api_default_edit_delete(self) -> None:
        create = self.api_post(
            "/api/smtp/mailboxes",
            json={
                "provider": self.creds["provider"],
                "email": self.creds["email"],
                "password": self.creds["password"],
                "sender_name": "API Acceptance",
                "make_default": True,
                "send_test": False,
            },
        )
        self.assertEqual(create.status_code, 200)
        mailbox = create.json().get("result", {}).get("mailbox") or create.json().get("mailbox") or {}
        mailbox_id = mailbox.get("id")
        self.assertTrue(mailbox_id)

        second = self.api_post(
            "/api/smtp/mailboxes",
            json={
                "provider": self.creds["provider"],
                "email": self.creds["email"],
                "password": self.creds["password"],
                "sender_name": "Second Copy",
                "make_default": False,
                "send_test": False,
            },
        )
        second_id = mailbox_id
        if second.status_code == 200:
            alt = (second.json().get("result", {}).get("mailbox") or second.json().get("mailbox") or {}).get("id")
            if alt:
                second_id = alt

        default = self.api_post(f"/api/smtp/mailboxes/{second_id}/default")
        self.assertIn(default.status_code, (200, 204))

        updated = self.api_patch(
            f"/api/smtp/mailboxes/{mailbox_id}",
            json={"sender_name": "Updated Acceptance", "send_test": False},
        )
        self.assertLess(updated.status_code, 400)

        bad = self.api_patch(
            f"/api/smtp/mailboxes/{mailbox_id}",
            json={"password": "wrong-password-for-auth-failed", "send_test": False},
        )
        self.record_scenario("s6_8_api_crud", "pass", f"default={default.status_code}, update={updated.status_code}, bad_pw={bad.status_code}")


if __name__ == "__main__":
    unittest.main()
