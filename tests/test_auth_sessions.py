from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from src.security.auth import principal_from_user_record
from src.security.auth_bootstrap import bootstrap_auth_store
from src.security.session_store import SESSION_COOKIE_NAME, get_session_username
from src.security.user_store import create_user
from src.utils.config import Settings
from src.web.auth_router import create_auth_router
from tests.bootstrap import PROJECT_ROOT, isolated_auth_db


class AuthSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._auth_ctx = isolated_auth_db()
        self.db_path = self._auth_ctx.__enter__()
        self.addCleanup(self._auth_ctx.__exit__, None, None, None)
        self.settings = Settings(
            app_password="admin-pass",
            app_username="admin",
            auth_db_path=str(self.db_path),
            app_session_ttl_days=7,
        )
        bootstrap_auth_store(self.settings)
        create_user("alice", "alice-pass-123")

        def check_auth(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
            username = get_session_username(session_token, ttl_days=self.settings.app_session_ttl_days)
            if not username:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth required")
            from src.security.user_store import get_user_record

            record = get_user_record(username)
            if record is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
            return principal_from_user_record(record)

        self.app = FastAPI()
        self.app.include_router(
            create_auth_router(
                settings_obj=self.settings,
                check_auth=check_auth,
                login_template_path=PROJECT_ROOT / "templates" / "login.html",
                register_template_path=PROJECT_ROOT / "templates" / "register.html",
            )
        )
        self.client = TestClient(self.app)

    def test_register_is_disabled_by_default(self) -> None:
        register_response = self.client.post(
            "/api/auth/register",
            json={
                "username": "bob",
                "password": "bob-pass-123",
                "password_confirm": "bob-pass-123",
            },
        )
        self.assertEqual(register_response.status_code, 404)

    def test_register_login_logout_and_me_when_enabled(self) -> None:
        self.settings.app_allow_registration = True
        register_response = self.client.post(
            "/api/auth/register",
            json={
                "username": "bob",
                "password": "bob-pass-123",
                "password_confirm": "bob-pass-123",
            },
        )
        self.assertEqual(register_response.status_code, 200)
        self.assertIn(SESSION_COOKIE_NAME, register_response.cookies)

        login_response = self.client.post(
            "/api/auth/login",
            json={"username": "bob", "password": "bob-pass-123"},
        )
        self.assertEqual(login_response.status_code, 200)
        token = login_response.cookies.get(SESSION_COOKIE_NAME)
        self.assertTrue(token)

        me_response = self.client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: token})
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["result"]["user"]["username"], "bob")

        logout_response = self.client.post("/api/auth/logout", cookies={SESSION_COOKIE_NAME: token})
        self.assertEqual(logout_response.status_code, 200)

        me_after_logout = self.client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: token})
        self.assertEqual(me_after_logout.status_code, 401)

    def test_login_rejects_invalid_password(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)


    def test_env_password_change_updates_existing_admin_user(self) -> None:
        first_login = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin-pass"},
        )
        self.assertEqual(first_login.status_code, 200)

        self.settings.app_password = "new-admin-pass"
        bootstrap_auth_store(self.settings)

        old_password = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin-pass"},
        )
        self.assertEqual(old_password.status_code, 401)

        new_password = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "new-admin-pass"},
        )
        self.assertEqual(new_password.status_code, 200)


if __name__ == "__main__":
    unittest.main()
