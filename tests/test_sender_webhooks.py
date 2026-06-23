from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.sender_router import create_sender_router


class SenderWebhookSecurityTests(unittest.TestCase):
    def _client(self, *, settings: SimpleNamespace, append_unisender: Mock | None = None, append_rusender: Mock | None = None) -> TestClient:
        app = FastAPI()
        app.include_router(
            create_sender_router(
                check_auth=lambda: "tester",
                parse_optional_limit=lambda payload: None,
                compact_sender_status=lambda state: state,
                clear_sender_stop_request=lambda job_id: None,
                prime_sender_checking_state=lambda job_id, transport, attachment_mode: {},
                prime_sender_running_state=lambda job_id, transport, attachment_mode: {},
                start_sender_thread_if_absent=lambda *args, **kwargs: (None, True),
                run_sender_background=lambda **kwargs: None,
                sender_job_key=lambda job_id: job_id or "default",
                get_sender_status=lambda job_id: {},
                get_generator_status=lambda job_id: {},
                get_unisender_history=lambda **kwargs: {},
                build_sender_delivery_analytics=lambda **kwargs: {},
                settings=settings,
                append_unisender_go_events=append_unisender or Mock(return_value={"saved": 1}),
                append_rusender_events=append_rusender or Mock(return_value={"saved": 1}),
                logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
                request_sender_stop=lambda **kwargs: {},
                preview_recipients=lambda **kwargs: {},
                chat_with_sender=lambda message, job_id=None: {"reply": ""},
                is_load_test_job=lambda job_id: False,
            )
        )
        return TestClient(app)

    def _settings(self, *, max_body_bytes: int = 1024) -> SimpleNamespace:
        return SimpleNamespace(
            unisender_webhook_token="secret-unisender",
            unisender_webhook_secret="",
            rusender_webhook_token="secret-rusender",
            rusender_webhook_secret="",
            webhook_max_body_bytes=max_body_bytes,
        )

    def test_unisender_webhook_rejects_invalid_token(self) -> None:
        append = Mock(return_value={"saved": 1})
        client = self._client(settings=self._settings(), append_unisender=append)

        response = client.post("/api/webhooks/unisender-go/wrong", json={"events": []})

        self.assertEqual(response.status_code, 401)
        append.assert_not_called()

    def test_unisender_webhook_rejects_oversized_body_before_append(self) -> None:
        append = Mock(return_value={"saved": 1})
        client = self._client(settings=self._settings(max_body_bytes=32), append_unisender=append)

        response = client.post(
            "/api/webhooks/unisender-go/secret-unisender",
            content=b'{"events":[{"data":"' + (b"x" * 64) + b'"}]}',
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 413)
        append.assert_not_called()

    def test_unisender_webhook_accepts_valid_token_and_limited_json(self) -> None:
        append = Mock(return_value={"saved": 1, "duplicates": 0})
        client = self._client(settings=self._settings(), append_unisender=append)

        response = client.post("/api/webhooks/unisender-go/secret-unisender", json={"events": []})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], {"saved": 1, "duplicates": 0})
        append.assert_called_once_with({"events": []})

    def test_rusender_webhook_rejects_invalid_token(self) -> None:
        append = Mock(return_value={"saved": 1})
        client = self._client(settings=self._settings(), append_rusender=append)

        response = client.post("/api/webhooks/rusender/wrong", json={"events": []})

        self.assertEqual(response.status_code, 401)
        append.assert_not_called()


if __name__ == "__main__":
    unittest.main()