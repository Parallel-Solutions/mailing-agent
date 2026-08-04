from __future__ import annotations

import unittest
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import SMTP as SMTP_POLICY
from unittest.mock import MagicMock, patch

from src.campaigns.batch_worker import (
    _SMTP_MESSAGE_POLICY,
    _send_smtp_message,
    _smtp_from_address,
)
from src.generator.delivery.smtp_mailboxes import ResolvedSmtpCredentials


class BatchWorkerSmtpHeaderTests(unittest.TestCase):
    @patch("src.generator.delivery.imap_sent.archive_sent_copy")
    @patch("src.generator.delivery.smtp_mailboxes._open_smtp_connection")
    @patch("src.generator.delivery.smtp_mailboxes.resolve_smtp_credentials")
    def test_sender_name_with_nested_quotes_is_serialized_safely(
        self,
        resolve_mock,
        open_mock,
        _archive_mock,
    ) -> None:
        sender_name = 'ООО "ЦИА "Случайный лес"'
        resolve_mock.return_value = ResolvedSmtpCredentials(
            email="personal.offer@parresh.ru",
            password="secret",
            host="smtp.mail.ru",
            port=465,
            use_ssl=True,
            use_starttls=False,
        )
        server = MagicMock()
        open_mock.return_value = server

        _send_smtp_message(
            mailbox_id="mailbox-1",
            owner_username="admin",
            to_email="recipient@example.com",
            subject="Test",
            html="<p>Test</p>",
            text="Test",
            sender_name=sender_name,
        )

        raw_message = server.sendmail.call_args.args[2]
        parsed = BytesParser(policy=SMTP_POLICY).parsebytes(raw_message)
        from_address = parsed["From"].addresses[0]
        self.assertEqual(from_address.display_name, sender_name)
        self.assertEqual(from_address.addr_spec, "personal.offer@parresh.ru")

    @patch("src.generator.delivery.imap_sent.archive_sent_copy")
    @patch("src.generator.delivery.smtp_mailboxes._open_smtp_connection")
    @patch("src.generator.delivery.smtp_mailboxes.resolve_smtp_credentials")
    def test_sender_name_rejects_header_line_breaks(
        self,
        resolve_mock,
        open_mock,
        _archive_mock,
    ) -> None:
        resolve_mock.return_value = ResolvedSmtpCredentials(
            email="personal.offer@parresh.ru",
            password="secret",
            host="smtp.mail.ru",
            port=465,
            use_ssl=True,
            use_starttls=False,
        )

        with self.assertRaisesRegex(ValueError, "line breaks"):
            _send_smtp_message(
                mailbox_id="mailbox-1",
                owner_username="admin",
                to_email="recipient@example.com",
                subject="Test",
                html="<p>Test</p>",
                text="Test",
                sender_name="Sender\r\nBcc: hidden@example.com",
            )

        open_mock.assert_not_called()

    def test_supported_sender_names_round_trip_through_mime(self) -> None:
        supported_names = (
            "",
            "Simple Sender",
            "\u041e\u041e\u041e \u00ab\u0426\u0418\u0410 \u0421\u043b\u0443\u0447\u0430\u0439\u043d\u044b\u0439 \u043b\u0435\u0441\u00bb",
            "\u0414\u043b\u0438\u043d\u043d\u043e\u0435 \u0438\u043c\u044f " * 20,
        )
        for sender_name in supported_names:
            with self.subTest(sender_name=sender_name):
                message = EmailMessage()
                message["From"] = _smtp_from_address(sender_name, "sender@example.com")
                parsed = BytesParser(policy=_SMTP_MESSAGE_POLICY).parsebytes(
                    message.as_bytes(policy=_SMTP_MESSAGE_POLICY)
                )
                from_address = parsed["From"].addresses[0]
                self.assertEqual(from_address.display_name, sender_name.strip())
                self.assertEqual(from_address.addr_spec, "sender@example.com")


if __name__ == "__main__":
    unittest.main()
