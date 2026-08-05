from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.security.auth import Principal
from src.web.sender_router import create_sender_router


class SenderRouterLegacyLockdownTests(unittest.TestCase):
    """Legacy xlsx sender (sender_agent.run_sender) has no UI and no
    open/click tracking. /api/sender/run is already disabled; this locks
    down the rest of its API surface that the frontend never calls, while
    keeping the shared admin endpoints (/resume, /suppression) alive.
    """

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(
            create_sender_router(
                check_auth=lambda: Principal("admin", "root", "admin"),
                parse_optional_limit=lambda payload: None,
                compact_sender_status=lambda state: state,
                clear_sender_stop_request=lambda job_id: None,
                prime_sender_checking_state=lambda *args, **kwargs: {},
                prime_sender_running_state=lambda *args, **kwargs: {},
                prime_sender_queued_state=lambda *args, **kwargs: {},
                prime_sender_scheduled_state=lambda *args, **kwargs: {},
                start_sender_thread_if_absent=lambda *args, **kwargs: (None, True),
                run_sender_background=lambda **kwargs: None,
                sender_job_key=lambda job_id: job_id or "default",
                get_sender_status=lambda job_id: {"status": "idle"},
                get_generator_status=lambda job_id: {},
                get_unisender_history=lambda **kwargs: {},
                build_sender_delivery_analytics=lambda **kwargs: {},
                settings=SimpleNamespace(
                    unisender_webhook_token="secret-unisender",
                    unisender_webhook_secret="",
                    rusender_webhook_token="secret-rusender",
                    rusender_webhook_secret="",
                    mailopost_webhook_token="secret-mailopost",
                    mailopost_webhook_secret="",
                    webhook_max_body_bytes=2048,
                ),
                append_unisender_go_events=lambda payload: {"saved": 1},
                append_rusender_events=lambda payload: {"saved": 1},
                append_mailopost_events=lambda payload: {"saved": 1},
                logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
                request_sender_stop=lambda **kwargs: {},
                preview_recipients=lambda **kwargs: {},
                chat_with_sender=lambda message, job_id=None, session_id=None: {"reply": message},
                is_load_test_job=lambda job_id: False,
            )
        )
        return TestClient(app)

    def test_sender_run_is_disabled(self) -> None:
        response = self._client().post("/api/sender/run", json={})
        self.assertEqual(response.status_code, 404)

    def test_sender_queue_is_disabled(self) -> None:
        response = self._client().get("/api/sender/queue")
        self.assertEqual(response.status_code, 404)

    def test_sender_scheduled_cancel_is_disabled(self) -> None:
        response = self._client().post("/api/sender/scheduled/cancel", json={})
        self.assertEqual(response.status_code, 404)

    def test_sender_domain_stats_is_disabled(self) -> None:
        response = self._client().get("/api/sender/domain-stats")
        self.assertEqual(response.status_code, 404)

    def test_sender_webhook_status_is_disabled(self) -> None:
        response = self._client().get("/api/sender/webhook-status")
        self.assertEqual(response.status_code, 404)

    def test_sender_status_is_disabled(self) -> None:
        response = self._client().get("/api/sender/status")
        self.assertEqual(response.status_code, 404)

    def test_sender_unisender_history_is_disabled(self) -> None:
        response = self._client().get("/api/sender/unisender-history")
        self.assertEqual(response.status_code, 404)

    def test_sender_analytics_is_disabled(self) -> None:
        response = self._client().get("/api/sender/analytics")
        self.assertEqual(response.status_code, 404)

    def test_sender_stop_is_disabled(self) -> None:
        response = self._client().post("/api/sender/stop", json={})
        self.assertEqual(response.status_code, 404)

    def test_sender_preview_is_disabled(self) -> None:
        response = self._client().post("/api/sender/preview", json={})
        self.assertEqual(response.status_code, 404)

    def test_sender_chat_is_disabled(self) -> None:
        response = self._client().post("/api/sender/chat", json={"message": "hi"})
        self.assertEqual(response.status_code, 404)

    def test_sender_resume_still_works(self) -> None:
        # /resume controls the shared send_guard (used by CampaignFlow too),
        # not legacy job state — it must stay reachable.
        from unittest.mock import patch

        with patch("src.generator.delivery.send_guard.resume_sending", lambda: None), patch(
            "src.generator.delivery.send_guard.get_send_guard_status", lambda: {"paused": False}
        ):
            response = self._client().post("/api/sender/resume")

        self.assertEqual(response.status_code, 200)

    def test_sender_suppression_list_still_works(self) -> None:
        # Shared stop-list admin API (suppression_store.py) also gates
        # CampaignFlow sends — it must stay reachable even without a UI.
        from unittest.mock import patch

        with patch("src.generator.delivery.suppression_store.list_suppressions", lambda **kwargs: []):
            response = self._client().get("/api/sender/suppression")

        self.assertEqual(response.status_code, 200)

    def test_consent_sales_requests_is_untouched(self) -> None:
        # Not a legacy-sender endpoint (consent domain) — must not be
        # accidentally locked down by this sweep.
        from unittest.mock import patch

        with patch("src.web.sender_router.build_sales_consent_requests", lambda job_id, include_all=False: []):
            response = self._client().get("/api/consents/sales-requests")

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
