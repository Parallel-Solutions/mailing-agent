from __future__ import annotations

import unittest

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, status
from fastapi.testclient import TestClient

from src.security.auth import principal_from_user_record
from src.security.auth_bootstrap import bootstrap_auth_store
from src.security.bearer_auth import resolve_request_username
from src.security.session_store import SESSION_COOKIE_NAME, create_session
from src.security.user_store import create_user, get_user_record
from src.utils.config import Settings
from tests.bootstrap import isolated_auth_db


class BearerAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._auth_ctx = isolated_auth_db()
        self._auth_ctx.__enter__()
        self.addCleanup(self._auth_ctx.__exit__, None, None, None)
        self.settings = Settings(
            app_password="admin-pass",
            app_username="admin",
            app_session_ttl_days=7,
            mailing_agent_mcp_tokens='{"mcp-static-token":"alice"}',
        )
        bootstrap_auth_store(self.settings)
        create_user("alice", "alice-pass-123")

        def check_auth(
            session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
            authorization: str | None = Header(default=None),
        ):
            username = resolve_request_username(
                session_token=session_token,
                authorization=authorization,
                settings_obj=self.settings,
            )
            if not username:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth required")
            record = get_user_record(username)
            if record is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid")
            return principal_from_user_record(record)

        app = FastAPI()

        @app.get("/secure")
        def secure(principal=Depends(check_auth)):
            return {"username": principal.username}

        self.client = TestClient(app)

    def test_static_mcp_bearer_token(self) -> None:
        response = self.client.get("/secure", headers={"Authorization": "Bearer mcp-static-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "alice")

    def test_session_bearer_token(self) -> None:
        token = create_session("alice", ttl_days=7)
        response = self.client.get("/secure", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "alice")

    def test_cookie_still_works(self) -> None:
        token = create_session("alice", ttl_days=7)
        response = self.client.get("/secure", cookies={SESSION_COOKIE_NAME: token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "alice")

    def test_invalid_bearer_rejected(self) -> None:
        response = self.client.get("/secure", headers={"Authorization": "Bearer nope"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
