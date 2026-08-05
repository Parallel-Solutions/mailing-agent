from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.infra.db import session_scope
from src.infra.models import SmtpMailbox
from src.workers.queue_worker import _run_inbox_bounce_scan_if_due
from tests.bootstrap import reset_test_database


class InboxBounceScanTickTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def _mailbox(self, **overrides) -> None:
        defaults = dict(
            id="mailbox-tick-1",
            owner_username="owner",
            email="sender@example.com",
            host="imap.example.com",
            bounce_scan_enabled=True,
            bounce_scan_last_checked_at=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        with session_scope() as session:
            session.add(SmtpMailbox(**defaults))

    def test_global_kill_switch_disabled_does_nothing(self) -> None:
        self._mailbox()
        with patch("src.workers.queue_worker.settings") as settings_mock:
            settings_mock.imap_bounce_scan_enabled = False
            result = _run_inbox_bounce_scan_if_due(0.0)

        self.assertEqual(result, 0.0)

    def test_enqueues_task_for_due_mailbox(self) -> None:
        self._mailbox(bounce_scan_last_checked_at=None)
        with (
            patch("src.workers.queue_worker.settings") as settings_mock,
            patch("src.workers.task_queue.enqueue_task") as enqueue_mock,
        ):
            settings_mock.imap_bounce_scan_enabled = True
            settings_mock.imap_bounce_scan_tick_seconds = 60
            settings_mock.imap_bounce_scan_interval_seconds = 1800
            enqueue_mock.return_value = ({"task_id": "t1"}, True)

            _run_inbox_bounce_scan_if_due(0.0)

        enqueue_mock.assert_called_once()
        self.assertEqual(enqueue_mock.call_args.kwargs["task_type"], "inbox_bounce_scan")
        self.assertEqual(enqueue_mock.call_args.kwargs["payload"]["mailbox_id"], "mailbox-tick-1")

    def test_skips_mailbox_recently_checked(self) -> None:
        self._mailbox(bounce_scan_last_checked_at=datetime.now(timezone.utc))
        with (
            patch("src.workers.queue_worker.settings") as settings_mock,
            patch("src.workers.task_queue.enqueue_task") as enqueue_mock,
        ):
            settings_mock.imap_bounce_scan_enabled = True
            settings_mock.imap_bounce_scan_tick_seconds = 60
            settings_mock.imap_bounce_scan_interval_seconds = 1800

            _run_inbox_bounce_scan_if_due(0.0)

        enqueue_mock.assert_not_called()

    def test_skips_mailbox_with_bounce_scan_disabled(self) -> None:
        self._mailbox(bounce_scan_enabled=False, bounce_scan_last_checked_at=None)
        with (
            patch("src.workers.queue_worker.settings") as settings_mock,
            patch("src.workers.task_queue.enqueue_task") as enqueue_mock,
        ):
            settings_mock.imap_bounce_scan_enabled = True
            settings_mock.imap_bounce_scan_tick_seconds = 60
            settings_mock.imap_bounce_scan_interval_seconds = 1800

            _run_inbox_bounce_scan_if_due(0.0)

        enqueue_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
