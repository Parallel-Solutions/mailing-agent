from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

from src.generator.delivery.imap_bounce import (
    _classify_and_apply,
    parse_arf,
    parse_dsn,
    scan_inbox_for_bounces,
)
from src.generator.delivery.imap_sent import ResolvedImapCredentials
from src.infra.db import session_scope
from src.infra.models import SmtpInboxEvent, SmtpMailbox, SmtpOpenTracking, SuppressionEntry
from tests.bootstrap import reset_test_database


def _build_dsn(
    *,
    final_recipient: str = "bounce@example.com",
    status: str = "5.1.1",
    action: str = "failed",
    diagnostic_code: str = "smtp; 550 5.1.1 user unknown",
    original_message_id: str | None = "<orig-1@example.com>",
    dsn_message_id: str = "<dsn-report-1@example.com>",
) -> bytes:
    outer = MIMEMultipart("report")
    outer.set_param("report-type", "delivery-status")
    outer["Subject"] = "Undelivered Mail Returned to Sender"
    outer["From"] = "mailer-daemon@example.com"
    outer["To"] = "sender@example.com"
    outer["Message-ID"] = dsn_message_id
    outer.attach(MIMEText("Your message could not be delivered.", _subtype="plain"))

    status_part = Message()
    status_part["Content-Type"] = "message/delivery-status"
    per_message = Message()
    per_message["Reporting-MTA"] = "dns; mail.example.com"
    per_recipient = Message()
    per_recipient["Final-Recipient"] = f"rfc822; {final_recipient}"
    per_recipient["Action"] = action
    per_recipient["Status"] = status
    per_recipient["Diagnostic-Code"] = diagnostic_code
    status_part.set_payload([per_message, per_recipient])
    outer.attach(status_part)

    if original_message_id:
        headers_part = Message()
        headers_part["Content-Type"] = "text/rfc822-headers"
        headers_part.set_payload(f"Message-ID: {original_message_id}\nTo: {final_recipient}\n")
        outer.attach(headers_part)

    return outer.as_bytes()


def _build_arf(
    *,
    feedback_type: str = "abuse",
    original_rcpt_to: str = "complainer@example.com",
) -> bytes:
    outer = MIMEMultipart("report")
    outer.set_param("report-type", "feedback-report")
    outer["Subject"] = "FW: complaint"
    outer.attach(MIMEText("This is an email abuse report.", _subtype="plain"))

    fb_part = Message()
    fb_part["Content-Type"] = "message/feedback-report"
    fb_fields = Message()
    fb_fields["Feedback-Type"] = feedback_type
    fb_fields["Original-Rcpt-To"] = original_rcpt_to
    fb_part.set_payload([fb_fields])
    outer.attach(fb_part)
    return outer.as_bytes()


class ParseDsnArfTests(unittest.TestCase):
    def test_parse_dsn_hard_bounce(self) -> None:
        report = parse_dsn(_build_dsn(status="5.1.1"))

        self.assertIsNotNone(report)
        self.assertEqual(report.final_recipient, "bounce@example.com")
        self.assertEqual(report.action, "failed")
        self.assertEqual(report.status_code, "5.1.1")
        self.assertEqual(report.original_message_id, "<orig-1@example.com>")

    def test_parse_dsn_soft_bounce(self) -> None:
        report = parse_dsn(_build_dsn(status="4.2.2", diagnostic_code="smtp; 452 4.2.2 mailbox full"))

        self.assertIsNotNone(report)
        self.assertEqual(report.status_code, "4.2.2")

    def test_parse_dsn_without_original_message_id(self) -> None:
        report = parse_dsn(_build_dsn(original_message_id=None))

        self.assertIsNotNone(report)
        self.assertEqual(report.original_message_id, "")

    def test_parse_dsn_rejects_non_dsn_message(self) -> None:
        self.assertIsNone(parse_dsn(_build_arf()))

    def test_parse_arf_abuse_complaint(self) -> None:
        report = parse_arf(_build_arf(feedback_type="abuse", original_rcpt_to="complainer@example.com"))

        self.assertIsNotNone(report)
        self.assertEqual(report.feedback_type, "abuse")
        self.assertEqual(report.original_rcpt_to, "complainer@example.com")

    def test_parse_arf_rejects_non_arf_message(self) -> None:
        self.assertIsNone(parse_arf(_build_dsn()))


class ClassifyAndApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()
        with session_scope() as session:
            session.add(
                SmtpMailbox(
                    id="mailbox-bounce-1",
                    owner_username="owner",
                    email="sender@example.com",
                    host="imap.example.com",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )

    def _seed_open_tracking(
        self,
        *,
        provider_message_id: str = "",
        recipient: str = "bounce@example.com",
        sent_at: datetime | None = None,
        job_id: str = "job-bounce-1",
    ) -> None:
        from uuid import uuid4

        with session_scope() as session:
            session.add(
                SmtpOpenTracking(
                    id=str(uuid4()),
                    token=uuid4().hex,
                    delivery_key_hash=uuid4().hex,
                    connection_id="mailbox-bounce-1",
                    owner_username="owner",
                    job_id=job_id,
                    recipient=recipient,
                    provider_message_id=provider_message_id or None,
                    sent_at=sent_at or datetime.now(timezone.utc),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )

    def test_hard_bounce_is_suppressed_and_matched_by_message_id(self) -> None:
        self._seed_open_tracking(provider_message_id="<orig-1@example.com>")
        raw = _build_dsn(status="5.1.1", original_message_id="<orig-1@example.com>")

        outcome = _classify_and_apply(connection_id="mailbox-bounce-1", raw_message=raw, imap_uid=1)

        self.assertEqual(outcome, "bounce")
        with session_scope() as session:
            entry = session.get(SuppressionEntry, "bounce@example.com")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.reason, "hard_bounce")
        events = self._load_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].matched_by, "message_id")
        self.assertEqual(events[0].matched_job_id, "job-bounce-1")
        self.assertTrue(events[0].suppression_applied)

    def test_soft_bounce_is_classified(self) -> None:
        raw = _build_dsn(status="4.2.2", diagnostic_code="smtp; 452 4.2.2 mailbox full", original_message_id=None)

        _classify_and_apply(connection_id="mailbox-bounce-1", raw_message=raw, imap_uid=2)

        with session_scope() as session:
            entry = session.get(SuppressionEntry, "bounce@example.com")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.reason, "soft_bounce")

    def test_arf_complaint_is_suppressed_as_spam(self) -> None:
        raw = _build_arf(feedback_type="abuse", original_rcpt_to="complainer@example.com")

        outcome = _classify_and_apply(connection_id="mailbox-bounce-1", raw_message=raw, imap_uid=3)

        self.assertEqual(outcome, "complaint")
        with session_scope() as session:
            entry = session.get(SuppressionEntry, "complainer@example.com")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.reason, "spam")

    def test_no_message_id_falls_back_to_recipient_window_match(self) -> None:
        self._seed_open_tracking(
            recipient="bounce@example.com",
            sent_at=datetime.now(timezone.utc) - timedelta(days=2),
            job_id="job-window-1",
        )
        raw = _build_dsn(status="5.1.1", original_message_id=None)

        _classify_and_apply(connection_id="mailbox-bounce-1", raw_message=raw, imap_uid=4)

        events = self._load_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].matched_by, "recipient_window")
        self.assertEqual(events[0].matched_job_id, "job-window-1")

    def test_suppression_applied_even_with_no_attribution_match(self) -> None:
        raw = _build_dsn(status="5.1.1", final_recipient="totally-unknown@example.com", original_message_id=None)

        _classify_and_apply(connection_id="mailbox-bounce-1", raw_message=raw, imap_uid=5)

        with session_scope() as session:
            entry = session.get(SuppressionEntry, "totally-unknown@example.com")
            self.assertIsNotNone(entry)
        events = self._load_events()
        self.assertEqual(events[0].matched_by, "none")
        self.assertTrue(events[0].suppression_applied)

    def test_duplicate_message_is_not_reprocessed(self) -> None:
        raw = _build_dsn(status="5.1.1", original_message_id="<dup-1@example.com>")

        first = _classify_and_apply(connection_id="mailbox-bounce-1", raw_message=raw, imap_uid=6)
        second = _classify_and_apply(connection_id="mailbox-bounce-1", raw_message=raw, imap_uid=7)

        self.assertEqual(first, "bounce")
        self.assertIsNone(second)
        self.assertEqual(len(self._load_events()), 1)

    def _load_events(self) -> list[SmtpInboxEvent]:
        from sqlalchemy import select

        with session_scope() as session:
            return list(session.scalars(select(SmtpInboxEvent)).all())


class ScanInboxForBouncesTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def _mailbox(self, **overrides) -> None:
        defaults = dict(
            id="mailbox-scan-1",
            owner_username="owner",
            email="sender@example.com",
            host="imap.example.com",
            bounce_scan_enabled=True,
            bounce_scan_last_uid=0,
            bounce_scan_uidvalidity=1000,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        with session_scope() as session:
            session.add(SmtpMailbox(**defaults))

    def test_scan_disabled_returns_early(self) -> None:
        self._mailbox(bounce_scan_enabled=False)

        result = scan_inbox_for_bounces(mailbox_id="mailbox-scan-1", owner_username="owner")

        self.assertEqual(result.status, "disabled")

    def test_scan_not_configured_for_missing_mailbox(self) -> None:
        result = scan_inbox_for_bounces(mailbox_id="does-not-exist", owner_username="owner")

        self.assertEqual(result.status, "not_configured")

    @patch("src.generator.delivery.imap_bounce._open_imap_connection")
    @patch("src.generator.delivery.imap_bounce.resolve_imap_credentials")
    def test_scan_processes_new_uids_and_persists_cursor(
        self,
        resolve_mock: MagicMock,
        open_mock: MagicMock,
    ) -> None:
        self._mailbox()
        resolve_mock.return_value = ResolvedImapCredentials(
            connection_id="mailbox-scan-1",
            email="sender@example.com",
            username="sender@example.com",
            password="secret",
            host="imap.example.com",
            port=993,
            use_ssl=True,
            use_starttls=False,
            sent_folder="Sent",
            auth_method="password",
            save_sent_copy=True,
        )
        client = MagicMock()
        client.select.return_value = ("OK", [b"1"])
        client.status.return_value = ("OK", [b"INBOX (UIDVALIDITY 1000)"])
        raw_dsn = _build_dsn(status="5.1.1", original_message_id=None)

        def _uid_side_effect(command, *args):
            if command == "SEARCH":
                return ("OK", [b"5"])
            if command == "FETCH":
                return ("OK", [(b"5 (BODY[] {1}", raw_dsn), b")"])
            return ("NO", [])

        client.uid.side_effect = _uid_side_effect
        open_mock.return_value = client

        result = scan_inbox_for_bounces(mailbox_id="mailbox-scan-1", owner_username="owner", max_messages=10)

        self.assertEqual(result.status, "scanned")
        self.assertEqual(result.messages_seen, 1)
        self.assertEqual(result.bounces_found, 1)
        client.logout.assert_called_once()

        with session_scope() as session:
            row = session.get(SmtpMailbox, "mailbox-scan-1")
            self.assertEqual(int(row.bounce_scan_last_uid), 5)
            self.assertEqual(int(row.bounce_scan_uidvalidity), 1000)
            self.assertIsNotNone(row.bounce_scan_last_checked_at)

    @patch("src.generator.delivery.imap_bounce._open_imap_connection")
    @patch("src.generator.delivery.imap_bounce.resolve_imap_credentials")
    def test_uidvalidity_change_resets_cursor(
        self,
        resolve_mock: MagicMock,
        open_mock: MagicMock,
    ) -> None:
        self._mailbox(bounce_scan_last_uid=50, bounce_scan_uidvalidity=1000)
        resolve_mock.return_value = ResolvedImapCredentials(
            connection_id="mailbox-scan-1",
            email="sender@example.com",
            username="sender@example.com",
            password="secret",
            host="imap.example.com",
            port=993,
            use_ssl=True,
            use_starttls=False,
            sent_folder="Sent",
            auth_method="password",
            save_sent_copy=True,
        )
        client = MagicMock()
        client.select.return_value = ("OK", [b"1"])
        # A different UIDVALIDITY means the mailbox was rebuilt.
        client.status.return_value = ("OK", [b"INBOX (UIDVALIDITY 2000)"])
        client.uid.side_effect = lambda command, *args: ("OK", [b""]) if command == "SEARCH" else ("NO", [])
        open_mock.return_value = client

        scan_inbox_for_bounces(mailbox_id="mailbox-scan-1", owner_username="owner")

        # SEARCH must have been issued for UID range starting at 1 (cursor reset),
        # not 51 (the stale cursor).
        search_calls = [c for c in client.uid.call_args_list if c.args[0] == "SEARCH"]
        self.assertTrue(any("1:*" in str(c) for c in search_calls))
        with session_scope() as session:
            row = session.get(SmtpMailbox, "mailbox-scan-1")
            self.assertEqual(int(row.bounce_scan_uidvalidity), 2000)


if __name__ == "__main__":
    unittest.main()
