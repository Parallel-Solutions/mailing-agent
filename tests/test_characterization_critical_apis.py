"""Characterization tests for critical existing APIs before UI migration.

These tests lock current contracts so refactors do not silently break auth,
SMTP mailboxes, sender scheduling, statistics surface, or consent routes.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.generator.delivery.smtp_mailboxes import create_mailbox, list_mailboxes
from src.security.auth import Principal
from src.web.auth_router import create_auth_router
from src.web.consent_router import create_consent_router
from src.web.smtp_router import create_smtp_router
from src.web.statistics_router import create_statistics_router
from tests.bootstrap import bootstrap_test_runtime


class AuthCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"u{uuid.uuid4().hex[:8]}"
        self.password = "TestPass123!"
        from src.security.user_store import create_user

        create_user(self.username, self.password)
        settings = SimpleNamespace(
            app_session_ttl_days=7,
            app_allow_registration=True,
            public_base_url="http://localhost:9806",
        )
        from pathlib import Path

        login_html = Path(__file__).resolve().parents[1] / "templates" / "login.html"
        register_html = Path(__file__).resolve().parents[1] / "templates" / "register.html"
        app = FastAPI()
        app.include_router(
            create_auth_router(
                settings_obj=settings,
                check_auth=lambda: Principal(self.username, "t1", "user"),
                login_template_path=login_html,
                register_template_path=register_html,
            )
        )
        self.client = TestClient(app)

    def test_login_success_sets_cookie_and_returns_user(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"username": self.username, "password": self.password},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["result"]["user"]["username"], self.username)
        self.assertTrue(any("session" in c.lower() or "mailing" in c.lower() for c in response.headers.get("set-cookie", "").lower().split(",") + [response.headers.get("set-cookie", "").lower()]))

    def test_login_wrong_password_returns_401(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"username": self.username, "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)

    def test_me_requires_auth_dependency(self) -> None:
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["user"]["username"], self.username)


class SmtpMailboxCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.owner = f"owner-{uuid.uuid4().hex[:8]}"

    def test_mailbox_secrets_never_returned(self) -> None:
        created = create_mailbox(
            owner_username=self.owner,
            provider="custom",
            email="mailpit@example.com",
            password="secret-app-password",
            host="mailpit",
            port=1025,
            use_ssl=False,
            use_starttls=False,
            make_default=True,
        )
        self.assertNotIn("password", created)
        self.assertNotIn("password_encrypted", created)
        listed = list_mailboxes(self.owner)
        self.assertEqual(len(listed), 1)
        self.assertNotIn("password", listed[0])

    def test_smtp_api_list_requires_owner_scope(self) -> None:
        create_mailbox(
            owner_username=self.owner,
            provider="custom",
            email="a@example.com",
            password="x",
            host="mailpit",
            port=1025,
        )
        app = FastAPI()
        app.include_router(create_smtp_router(check_auth=lambda: Principal(self.owner, "t1", "user")))
        client = TestClient(app)
        response = client.get("/api/smtp/mailboxes")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        items = body.get("result") or body.get("mailboxes") or body
        if isinstance(items, dict):
            items = items.get("mailboxes") or items.get("items") or []
        self.assertTrue(isinstance(items, list))
        self.assertGreaterEqual(len(items), 1)


class StatisticsSurfaceCharacterizationTests(unittest.TestCase):
    def test_statistics_router_exposes_manager_endpoints(self) -> None:
        app = FastAPI()
        app.include_router(
            create_statistics_router(
                check_auth=lambda: Principal("admin", "root", "admin"),
                jobs_dir=MagicMock(),
                resolve_job_paths=MagicMock(),
                logger=SimpleNamespace(exception=lambda *a, **k: None, info=lambda *a, **k: None, warning=lambda *a, **k: None),
            )
        )
        client = TestClient(app)
        routes = {getattr(r, "path", "") for r in app.routes}
        expected = {
            "/api/sender/campaigns",
            "/api/sender/recipients",
            "/api/sender/consents",
            "/api/sender/email-problems",
            "/api/sender/manager-dashboard",
            "/api/sender/reports",
        }
        self.assertTrue(expected.issubset(routes), f"Missing routes: {expected - routes}")


class ConsentRouteCharacterizationTests(unittest.TestCase):
    def test_consent_public_routes_registered(self) -> None:
        app = FastAPI()
        app.include_router(create_consent_router())
        routes = {getattr(r, "path", "") for r in app.routes}
        self.assertIn("/consent/request/{token}", routes)
        self.assertIn("/consent/confirm/{token}", routes)


class SenderScheduleContractCharacterizationTests(unittest.TestCase):
    def test_sender_run_request_accepts_scheduled_start_at(self) -> None:
        from src.web.request_models import SenderRunRequest

        future = datetime.now(timezone.utc) + timedelta(hours=2)
        model = SenderRunRequest(
            job_id="job-char-1",
            dry_run=True,
            scheduled_start_at=future.isoformat(),
            campaign_name="Char Campaign",
        )
        self.assertIsNotNone(model.scheduled_start_at)
        self.assertEqual(model.campaign_name, "Char Campaign")


if __name__ == "__main__":
    unittest.main()
