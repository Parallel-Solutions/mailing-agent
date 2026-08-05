from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.campaigns.batch_worker import _send_delivery_message
from src.campaigns.connection_service import ResolvedConnection
from src.generator.delivery.click_tracking import (
    TOKEN_RE,
    load_smtp_click_events,
    mark_smtp_click_tracking_sent,
    record_smtp_click,
    rewrite_smtp_click_links,
)
from src.generator.delivery.sender_report import _match_smtp_click_event
from src.web.public_router import create_public_router
from tests.bootstrap import reset_test_database


class SmtpClickTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def _kwargs(self, **overrides) -> dict:
        base = {
            "delivery_key": "conn-1\x1fcampaign-1\x1fjob-1\x1f42\x1femail\x1f\x1f\x1fto@example.com",
            "connection_id": "",
            "owner_username": "owner",
            "recipient": "to@example.com",
            "job_id": "job-1",
            "campaign_id": "",
            "row_id": "42",
            "send_mode": "email",
            "warmup_delivery_id": "",
        }
        base.update(overrides)
        return base

    def test_rewrite_href_and_bare_url_links(self) -> None:
        html = '<p><a href="https://example.com/a">Link A</a></p>'
        text = "Also visit https://example.com/b for more."

        tracked_html, tracked_text, tokens = rewrite_smtp_click_links(html, text, **self._kwargs())

        self.assertEqual(len(tokens), 2)
        for token in tokens:
            self.assertRegex(token, TOKEN_RE)
        self.assertIn("/public/email/click/", tracked_html)
        self.assertIn("/public/email/click/", tracked_text)
        self.assertNotIn("https://example.com/a", tracked_html)
        self.assertNotIn("https://example.com/b", tracked_text)

    def test_rewrite_ignores_mailto_and_non_http_links(self) -> None:
        html = '<p><a href="mailto:someone@example.com">mail</a></p>'

        tracked_html, tracked_text, tokens = rewrite_smtp_click_links(html, "", **self._kwargs())

        self.assertEqual(tokens, [])
        self.assertIn("mailto:someone@example.com", tracked_html)

    def test_rewrite_is_stable_across_retries(self) -> None:
        html = '<a href="https://example.com/retry">Retry link</a>'
        kwargs = self._kwargs()

        _, _, tokens_first = rewrite_smtp_click_links(html, "", **kwargs)
        _, _, tokens_second = rewrite_smtp_click_links(html, "", **kwargs)

        self.assertEqual(tokens_first, tokens_second)

    def test_record_smtp_click_dedup_window_and_invalid_token(self) -> None:
        html = '<a href="https://example.com/click-me">Click</a>'
        _, _, tokens = rewrite_smtp_click_links(html, "", **self._kwargs())
        token = tokens[0]

        first = record_smtp_click(token)
        second = record_smtp_click(token)

        self.assertTrue(first["found"])
        self.assertEqual(first["target_url"], "https://example.com/click-me")
        self.assertTrue(second["found"])

        events = load_smtp_click_events("job-1")
        self.assertEqual(len(events), 1)
        # Same-second re-click is inside the dedup window, so click_count stays at 1.
        self.assertEqual(events[0]["click_count"], 1)

        invalid = record_smtp_click("not-a-valid-token")
        self.assertFalse(invalid["found"])
        self.assertEqual(invalid["target_url"], "")

    @patch("src.campaigns.batch_worker._record_smtp_accept")
    @patch("src.generator.delivery.click_tracking.mark_smtp_click_tracking_sent")
    @patch("src.generator.delivery.open_tracking.mark_smtp_open_tracking_sent")
    @patch("src.generator.delivery.open_tracking.prepare_smtp_open_tracking", return_value=None)
    @patch("src.campaigns.batch_worker._send_smtp_message", return_value="<message-click-1@example.test>")
    @patch("src.campaigns.connection_service.resolve_connection")
    @patch("src.generator.delivery.channel_guard.wait_for_channel_send_slot")
    def test_send_delivery_message_rewrites_links_and_marks_click_tokens_sent(
        self,
        wait_mock,
        resolve_mock,
        send_mock,
        prepare_open_mock,
        mark_open_mock,
        mark_click_mock,
        accept_mock,
    ) -> None:
        resolve_mock.return_value = ResolvedConnection(
            id="conn-click-1",
            transport="smtp",
            email="sender@example.com",
            sender_name="Sender",
            secret="",
            api_base_url="",
        )
        # SmtpClickTracking.connection_id is FK-constrained to smtp_mailboxes;
        # a real (if minimal) row is needed for the insert to succeed.
        from datetime import datetime, timezone

        from src.infra.db import session_scope
        from src.infra.models import SmtpMailbox

        with session_scope() as session:
            session.add(
                SmtpMailbox(
                    id="conn-click-1",
                    owner_username="owner",
                    email="sender@example.com",
                    host="smtp.example.com",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )

        result = _send_delivery_message(
            connection_id="conn-click-1",
            owner_username="owner",
            to_email="to@example.com",
            subject="Subject",
            html='<p><a href="https://example.com/product">Product</a></p>',
            text="See https://example.com/product",
            job_id="job-click-1",
            row_id="7",
        )

        self.assertEqual(result, "<message-click-1@example.test>")
        sent_html = send_mock.call_args.kwargs["html"]
        self.assertIn("/public/email/click/", sent_html)
        self.assertNotIn("https://example.com/product", sent_html)
        mark_click_mock.assert_called_once()
        called_tokens = mark_click_mock.call_args.args[0]
        self.assertEqual(len(called_tokens), 1)
        accept_mock.assert_called_once()

    @patch("src.campaigns.batch_worker._record_smtp_accept")
    @patch("src.generator.delivery.click_tracking.mark_smtp_click_tracking_sent")
    @patch("src.generator.delivery.open_tracking.mark_smtp_open_tracking_sent")
    @patch("src.generator.delivery.open_tracking.prepare_smtp_open_tracking", return_value=None)
    @patch("src.campaigns.batch_worker._send_smtp_message", return_value="<message-chain-1@example.test>")
    @patch("src.campaigns.connection_service.resolve_connection")
    @patch("src.generator.delivery.channel_guard.wait_for_channel_send_slot")
    def test_send_delivery_message_skips_click_tracking_for_email_chain_campaigns(
        self,
        wait_mock,
        resolve_mock,
        send_mock,
        prepare_open_mock,
        mark_open_mock,
        mark_click_mock,
        accept_mock,
    ) -> None:
        # Email chains already tokenize their own links to /chain/content/...
        # (chain_send_service.py) independent of transport; wrapping them
        # again here would just be a redundant extra redirect that nothing
        # reads stats from.
        from types import SimpleNamespace

        from datetime import datetime, timezone

        from src.infra.db import session_scope
        from src.infra.models import SmtpMailbox

        resolve_mock.return_value = ResolvedConnection(
            id="conn-chain-1",
            transport="smtp",
            email="sender@example.com",
            sender_name="Sender",
            secret="",
            api_base_url="",
        )
        with session_scope() as session:
            session.add(
                SmtpMailbox(
                    id="conn-chain-1",
                    owner_username="owner",
                    email="sender@example.com",
                    host="smtp.example.com",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )

        chain_campaign = SimpleNamespace(id="campaign-chain-1", send_scenario="email_chain", email_chain_id=None)

        result = _send_delivery_message(
            connection_id="conn-chain-1",
            owner_username="owner",
            to_email="to@example.com",
            subject="Subject",
            html='<p><a href="https://chain.example.com/chain/content/tok123">Product</a></p>',
            text="See https://chain.example.com/chain/content/tok123",
            job_id="job-chain-1",
            row_id="7",
            campaign=chain_campaign,
        )

        self.assertEqual(result, "<message-chain-1@example.test>")
        sent_html = send_mock.call_args.kwargs["html"]
        # Unchanged: no second /public/email/click/ redirect wrapped around
        # the chain's own link.
        self.assertNotIn("/public/email/click/", sent_html)
        self.assertIn("/chain/content/tok123", sent_html)
        mark_click_mock.assert_not_called()
        self.assertEqual(load_smtp_click_events("job-chain-1"), [])

    def test_match_smtp_click_event(self) -> None:
        event = {
            "provider_status": "clicked",
            "provider_message_id": "<message-1@example.test>",
            "recipient": "to@example.com",
            "row_id": "42",
            "click_count": 3,
        }
        events = {"message_email:<message-1@example.test>:to@example.com": event}
        item = {
            "transport": "smtp",
            "provider_message_id": "<message-1@example.test>",
            "recipient": "to@example.com",
            "row_id": "42",
        }

        self.assertEqual(_match_smtp_click_event(item, events), event)
        self.assertIsNone(_match_smtp_click_event({**item, "transport": "rusender"}, events))

    def test_public_click_redirect_valid_token(self) -> None:
        html = '<a href="https://example.com/redirect-target">Go</a>'
        _, _, tokens = rewrite_smtp_click_links(html, "", **self._kwargs())
        token = tokens[0]

        app = FastAPI()
        app.include_router(create_public_router())
        client = TestClient(app, follow_redirects=False)

        response = client.get(f"/public/email/click/{token}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "https://example.com/redirect-target")

    def test_public_click_redirect_invalid_token_falls_back_to_base_url(self) -> None:
        app = FastAPI()
        app.include_router(create_public_router())
        client = TestClient(app, follow_redirects=False)

        response = client.get("/public/email/click/not-a-real-token")

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.headers["location"], "")

    def test_public_click_redirect_hides_recording_failure(self) -> None:
        app = FastAPI()
        app.include_router(create_public_router())
        client = TestClient(app, follow_redirects=False)

        with patch(
            "src.generator.delivery.click_tracking.record_smtp_click",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = client.get("/public/email/click/whatever-token")

        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
