from __future__ import annotations

import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.infra.db import session_scope
from src.infra.models import UserOnboardingState
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class OnboardingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"o{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")

        app = FastAPI()
        app.include_router(
            create_v1_router(check_auth=lambda: Principal(self.username, self.username, "user"))
        )
        self.client = TestClient(app)

    def test_new_user_can_pause_and_restart_tour(self) -> None:
        initial = self.client.get("/api/v1/onboarding")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["result"]["status"], "active")
        self.assertEqual(initial.json()["result"]["current_step"], 0)
        self.assertEqual(initial.json()["result"]["version"], 5)
        self.assertEqual(initial.json()["result"]["step_count"], 24)

        paused = self.client.patch(
            "/api/v1/onboarding",
            json={
                "status": "paused",
                "current_step": 3,
                "completed_steps": ["welcome", "connection-open", "connection-method"],
            },
        )
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["result"]["status"], "paused")
        self.assertEqual(paused.json()["result"]["current_step"], 3)

        restarted = self.client.post("/api/v1/onboarding/restart")
        self.assertEqual(restarted.status_code, 200)
        self.assertEqual(restarted.json()["result"]["status"], "active")
        self.assertEqual(restarted.json()["result"]["current_step"], 0)
        self.assertEqual(restarted.json()["result"]["completed_steps"], [])

    def test_account_without_state_does_not_start_automatically(self) -> None:
        with session_scope() as session:
            row = session.get(UserOnboardingState, self.username)
            self.assertIsNotNone(row)
            session.delete(row)

        response = self.client.get("/api/v1/onboarding")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["status"], "dismissed")


if __name__ == "__main__":
    unittest.main()
