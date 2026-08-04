from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.campaigns.batch_worker import _send_delivery_message
from src.campaigns.connection_service import ResolvedConnection
from src.generator.delivery.open_tracking import (
    PIXEL_MARKER,
    PreparedSmtpOpenTracking,
    TRANSPARENT_GIF,
    build_smtp_tracking_delivery_key,
    inject_smtp_open_tracking_pixel,
)
from src.generator.delivery.sender_report import _match_smtp_open_event
from src.web.public_router import create_public_router


class SmtpOpenTrackingTests(unittest.TestCase):
    def test_delivery_key_is_stable_for_retry_and_changes_for_new_run(self) -> None:
        kwargs = {
            "connection_id": "conn-1",
            "recipient": "USER@example.com",
            "job_id": "job-1",
            "campaign_id": "campaign-1",
            "row_id": "42",
            "send_mode": "email",
        }
        first = build_smtp_tracking_delivery_key(**kwargs)
        retry = build_smtp_tracking_delivery_key(**kwargs)
        resend = build_smtp_tracking_delivery_key(**kwargs, send_run_id="run-2")

        self.assertEqual(first, retry)
        self.assertNotEqual(first, resend)
        self.assertNotIn("USER@example.com", first)

    def test_pixel_is_inserted_before_body_and_not_duplicated(self) -> None:
        html = "<html><body><p>Hello</p></body></html>"
        pixel_url = "https://example.test/public/email/open/token_123.gif"

        tracked = inject_smtp_open_tracking_pixel(html, pixel_url)
        tracked_twice = inject_smtp_open_tracking_pixel(tracked, pixel_url)

        self.assertIn(PIXEL_MARKER, tracked)
        self.assertLess(tracked.index(PIXEL_MARKER), tracked.lower().index("</body>"))
        self.assertEqual(tracked, tracked_twice)

    @patch("src.campaigns.batch_worker._record_smtp_accept")
    @patch("src.generator.delivery.open_tracking.mark_smtp_open_tracking_sent")
    @patch("src.generator.delivery.open_tracking.prepare_smtp_open_tracking")
    @patch("src.campaigns.batch_worker._send_smtp_message", return_value="<message-1@example.test>")
    @patch("src.campaigns.connection_service.resolve_connection")
    @patch("src.generator.delivery.channel_guard.wait_for_channel_send_slot")
    def test_smtp_delivery_injects_pixel_and_marks_tracking_sent(
        self,
        wait_mock,
        resolve_mock,
        send_mock,
        prepare_mock,
        mark_mock,
        accept_mock,
    ) -> None:
        resolve_mock.return_value = ResolvedConnection(
            id="conn-1",
            transport="smtp",
            email="sender@example.com",
            sender_name="Sender",
            secret="",
            api_base_url="",
        )
        prepare_mock.return_value = PreparedSmtpOpenTracking(
            token="a" * 43,
            pixel_url="https://example.test/public/email/open/" + "a" * 43 + ".gif",
        )

        result = _send_delivery_message(
            connection_id="conn-1",
            owner_username="owner",
            to_email="to@example.com",
            subject="Subject",
            html="<p>Hello</p>",
            text="Hello",
            job_id="job-1",
            row_id="42",
        )

        self.assertEqual(result, "<message-1@example.test>")
        self.assertIn(PIXEL_MARKER, send_mock.call_args.kwargs["html"])
        mark_mock.assert_called_once_with("a" * 43, "<message-1@example.test>")
        accept_mock.assert_called_once()
        wait_mock.assert_called_once_with("conn-1", allow_warmup=False)

    def test_smtp_open_event_matches_exact_message(self) -> None:
        event = {
            "provider_status": "opened",
            "provider_message_id": "<message-1@example.test>",
            "recipient": "to@example.com",
            "row_id": "42",
            "open_count": 2,
        }
        events = {
            "message_email:<message-1@example.test>:to@example.com": event,
        }
        item = {
            "transport": "smtp",
            "provider_message_id": "<message-1@example.test>",
            "recipient": "to@example.com",
            "row_id": "42",
        }

        self.assertEqual(_match_smtp_open_event(item, events), event)
        self.assertIsNone(_match_smtp_open_event({**item, "transport": "rusender"}, events))

    def test_public_pixel_always_returns_non_cached_gif(self) -> None:
        app = FastAPI()
        app.include_router(create_public_router())
        client = TestClient(app)
        token = "a" * 43

        with patch("src.generator.delivery.open_tracking.record_smtp_open") as record_mock:
            response = client.get(f"/public/email/open/{token}.gif")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, TRANSPARENT_GIF)
        self.assertEqual(response.headers["content-type"], "image/gif")
        self.assertIn("no-store", response.headers["cache-control"])
        record_mock.assert_called_once_with(token)

    def test_public_pixel_hides_recording_failure(self) -> None:
        app = FastAPI()
        app.include_router(create_public_router())
        client = TestClient(app)

        with patch(
            "src.generator.delivery.open_tracking.record_smtp_open",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = client.get("/public/email/open/not-a-real-token.gif")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, TRANSPARENT_GIF)


if __name__ == "__main__":
    unittest.main()
