from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from src.generator.delivery.smtp_autodiscover import SmtpDiscoveryResult
from src.generator.delivery.smtp_probe import (
    ProbeAttempt,
    _probe_transport,
    probe_smtp_for_email,
)


class SmtpProbeTests(unittest.TestCase):
    @patch("src.generator.delivery.smtp_probe._probe_transport")
    @patch("src.generator.delivery.smtp_probe.discover_smtp_candidates")
    def test_prefers_first_reachable_transport(self, mock_discover, mock_probe) -> None:
        mock_discover.return_value = [
            SmtpDiscoveryResult(
                provider="mailru",
                host="smtp.mail.ru",
                port=465,
                use_ssl=True,
                use_starttls=False,
                source="preset",
                confidence="high",
            )
        ]
        mock_probe.side_effect = [
            ProbeAttempt("smtp.mail.ru", 465, True, False, False, error="ssl failed"),
            ProbeAttempt("smtp.mail.ru", 587, False, True, True, banner="220 ready"),
        ]
        result, discoveries = probe_smtp_for_email("user@mail.ru")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.reachable)
        self.assertEqual(result.port, 587)
        self.assertFalse(result.use_ssl)
        self.assertTrue(result.use_starttls)
        self.assertEqual(len(discoveries), 1)

    @patch("src.generator.delivery.smtp_probe.smtplib.SMTP")
    def test_starttls_uses_smtplib(self, mock_smtp_cls: MagicMock) -> None:
        server = MagicMock()
        server.ehlo.side_effect = [(250, "smtp.test ESMTP"), (250, "smtp.test ESMTP")]
        mock_smtp_cls.return_value = server

        attempt = _probe_transport("smtp.test", 587, use_ssl=False, use_starttls=True)

        self.assertTrue(attempt.reachable)
        mock_smtp_cls.assert_called_once_with("smtp.test", 587, timeout=5)
        server.starttls.assert_called_once()
        server.quit.assert_called_once()

    @patch("src.generator.delivery.smtp_probe.ssl.create_default_context")
    @patch("src.generator.delivery.smtp_probe._resolve_ipv4_addresses", return_value=["94.100.180.160"])
    @patch("src.generator.delivery.smtp_probe.socket.create_connection")
    def test_implicit_ssl_uses_ipv4_connection(self, mock_connect, _resolve, mock_ssl_context) -> None:
        sock = mock_connect.return_value
        sock.recv.return_value = b"220 smtp.test ESMTP"
        wrapped = mock_ssl_context.return_value.wrap_socket.return_value
        wrapped.recv.return_value = b"220 smtp.test ESMTP"
        attempt = _probe_transport("smtp.test", 465, use_ssl=True, use_starttls=False)
        self.assertTrue(attempt.reachable)
        mock_connect.assert_called_with(("94.100.180.160", 465), timeout=5)

    @patch("src.generator.delivery.smtp_probe._resolve_ipv4_addresses", return_value=[])
    def test_no_ipv4_address_marks_unreachable(self, *_mocks: object) -> None:
        attempt = _probe_transport("smtp.test", 465, use_ssl=True, use_starttls=False)
        self.assertFalse(attempt.reachable)
        self.assertEqual(attempt.error, "no_ipv4_address")

    @patch("src.generator.delivery.smtp_probe._probe_transport")
    @patch("src.generator.delivery.smtp_probe.discover_smtp_candidates")
    def test_respects_probe_deadline(self, mock_discover, mock_probe) -> None:
        mock_discover.return_value = [
            SmtpDiscoveryResult(
                provider="custom",
                host="smtp.example.test",
                port=587,
                use_ssl=False,
                use_starttls=True,
                source="thunderbird",
                confidence="medium",
            )
        ]

        def slow_probe(*_args, **_kwargs):
            time.sleep(0.05)
            return ProbeAttempt("smtp.example.test", 587, False, True, False, error="slow")

        mock_probe.side_effect = slow_probe
        result, _discoveries = probe_smtp_for_email(
            "user@example.test",
            deadline=time.monotonic() + 0.01,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.reachable)
        self.assertLessEqual(mock_probe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
