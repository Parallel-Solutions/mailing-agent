from __future__ import annotations

import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from src.generator.delivery import sender_agent


class SenderAgentScalabilityTests(unittest.TestCase):
    def test_state_rows_snapshot_keeps_recent_entries(self) -> None:
        rows = [{"id": index} for index in range(sender_agent.SENDER_STATE_ROWS_LIMIT + 25)]

        snapshot = sender_agent._state_rows_snapshot(rows)

        self.assertEqual(len(snapshot), sender_agent.SENDER_STATE_ROWS_LIMIT)
        self.assertEqual(snapshot[0]["id"], 25)
        self.assertEqual(snapshot[-1]["id"], sender_agent.SENDER_STATE_ROWS_LIMIT + 24)

    def test_should_flush_sender_workbook_every_batch_and_on_finish(self) -> None:
        self.assertFalse(
            sender_agent._should_flush_sender_workbook(dirty=False, processed_rows=25, total_rows=100)
        )
        self.assertTrue(
            sender_agent._should_flush_sender_workbook(
                dirty=True,
                processed_rows=sender_agent.SENDER_WORKBOOK_SAVE_EVERY,
                total_rows=100,
            )
        )
        self.assertTrue(
            sender_agent._should_flush_sender_workbook(dirty=True, processed_rows=100, total_rows=100)
        )

    def test_unisender_parallel_workers_only_for_real_unisender_send(self) -> None:
        with patch.object(sender_agent.settings, "sender_unisender_concurrency", 7):
            self.assertEqual(sender_agent._unisender_parallel_workers(dry_run=False, transport="unisender"), 7)
            self.assertEqual(sender_agent._unisender_parallel_workers(dry_run=True, transport="unisender"), 1)
            self.assertEqual(sender_agent._unisender_parallel_workers(dry_run=False, transport="smtp"), 1)

    def test_run_unisender_request_retries_temporary_network_error(self) -> None:
        attempts = {"count": 0}

        def fake_urlopen(request: Request, timeout: float = 60):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise URLError("temporary network issue")

            class _Response:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return b'{"status":"success"}'

            return _Response()

        with patch.object(sender_agent, "urlopen", side_effect=fake_urlopen), patch.object(
            sender_agent, "_sleep_sender_retry", return_value=None
        ):
            raw = sender_agent._run_unisender_request(
                Request("https://example.com"),
                timeout=5,
                request_label="UniSender Go",
            )

        self.assertEqual(raw, '{"status":"success"}')
        self.assertEqual(attempts["count"], 3)

    def test_run_unisender_request_keeps_last_http_error_body(self) -> None:
        def raise_http_error(request: Request, timeout: float = 60):
            raise HTTPError(
                url="https://example.com",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"temporary outage"}'),
            )

        with patch.object(sender_agent, "urlopen", side_effect=raise_http_error), patch.object(
            sender_agent, "_sleep_sender_retry", return_value=None
        ):
            with self.assertRaises(HTTPError) as caught:
                sender_agent._run_unisender_request(
                    Request("https://example.com"),
                    timeout=5,
                    request_label="UniSender Go",
                )

        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(getattr(caught.exception, "raw_body", ""), '{"message":"temporary outage"}')


if __name__ == "__main__":
    unittest.main()
