from __future__ import annotations

import unittest
from email import message_from_bytes
from unittest.mock import MagicMock, patch

from src.campaigns.batch_worker import _send_smtp_message
from src.generator.delivery.imap_sent import (
    ResolvedImapCredentials,
    SentCopyResult,
    _append_once,
    _imap_utf7_decode,
    _imap_utf7_encode,
    archive_sent_copy,
)
from src.generator.delivery.smtp_mailboxes import ResolvedSmtpCredentials


class ImapSentEncodingTests(unittest.TestCase):
    def test_modified_utf7_round_trip(self) -> None:
        folder = "Отправленные & архив"
        self.assertEqual(_imap_utf7_decode(_imap_utf7_encode(folder)), folder)


class ImapSentArchiveTests(unittest.TestCase):
    def _credentials(self) -> ResolvedImapCredentials:
        return ResolvedImapCredentials(
            connection_id="mailbox-1",
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

    @patch("src.generator.delivery.imap_sent._open_imap_connection")
    def test_existing_message_is_not_appended_twice(self, open_mock: MagicMock) -> None:
        client = MagicMock()
        client.list.return_value = ("OK", [b'(\\HasNoChildren \\Sent) "/" "Sent"'])
        client.select.return_value = ("OK", [b"1"])
        client.uid.return_value = ("OK", [b"42"])
        open_mock.return_value = client

        result = _append_once(
            self._credentials(),
            raw_message=b"Message-ID: <message@example.com>\r\n\r\nBody",
            message_id="<message@example.com>",
        )

        self.assertEqual(result.status, "already_present")
        self.assertEqual(result.uid, "42")
        client.append.assert_not_called()

    @patch("src.generator.delivery.imap_sent._record_outcome")
    @patch("src.generator.delivery.imap_sent._append_once")
    @patch("src.generator.delivery.imap_sent.resolve_imap_credentials")
    def test_imap_failure_is_returned_without_raising(
        self,
        resolve_mock: MagicMock,
        append_mock: MagicMock,
        record_mock: MagicMock,
    ) -> None:
        resolve_mock.return_value = self._credentials()
        append_mock.side_effect = OSError("imap unavailable")

        result = archive_sent_copy(
            mailbox_id="mailbox-1",
            owner_username="owner",
            recipient="to@example.com",
            raw_message=b"message",
            message_id="<message@example.com>",
            max_attempts=2,
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("imap unavailable", result.error)
        self.assertEqual(append_mock.call_count, 2)
        record_mock.assert_called_once()


class BatchWorkerSmtpArchiveTests(unittest.TestCase):
    @patch("src.generator.delivery.imap_sent.archive_sent_copy")
    @patch("src.generator.delivery.smtp_mailboxes._open_smtp_connection")
    @patch("src.generator.delivery.smtp_mailboxes.resolve_smtp_credentials")
    def test_smtp_uses_same_mime_bytes_for_send_and_archive(
        self,
        resolve_mock: MagicMock,
        open_mock: MagicMock,
        archive_mock: MagicMock,
    ) -> None:
        resolve_mock.return_value = ResolvedSmtpCredentials(
            email="sender@example.com",
            password="secret",
            host="smtp.example.com",
            port=587,
            use_ssl=False,
            use_starttls=True,
            smtp_username="sender@example.com",
            mailbox_id="mailbox-1",
        )
        server = MagicMock()
        open_mock.return_value = server
        archive_mock.side_effect = RuntimeError("unexpected IMAP failure")

        message_id = _send_smtp_message(
            mailbox_id="mailbox-1",
            owner_username="owner",
            to_email="to@example.com",
            subject="Subject",
            html="<p>Hello</p>",
            text="Hello",
        )

        envelope_from, recipients, raw_message = server.sendmail.call_args.args
        self.assertEqual(envelope_from, "sender@example.com")
        self.assertEqual(recipients, ["to@example.com"])
        parsed = message_from_bytes(raw_message)
        self.assertEqual(parsed["Message-ID"], message_id)
        self.assertTrue(parsed["Date"])
        self.assertEqual(archive_mock.call_args.kwargs["raw_message"], raw_message)
        self.assertEqual(archive_mock.call_args.kwargs["message_id"], message_id)
        server.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
