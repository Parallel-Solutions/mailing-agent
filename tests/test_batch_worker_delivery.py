from __future__ import annotations

import unittest
from unittest.mock import patch

from src.campaigns.batch_worker import _send_delivery_message
from src.campaigns.connection_service import ResolvedConnection


class BatchWorkerDeliveryTests(unittest.TestCase):
    def _chain_html(self) -> str:
        return (
            '<p>Hello</p>'
            '<a href="http://localhost:8006/chain/branch/uuid-1" '
            'style="background:#1677ff">Получить</a>'
        )

    def _chain_text(self) -> str:
        return "Hello\n\nПолучить: http://localhost:8006/chain/branch/uuid-1"

    @patch("src.generator.delivery.sender_agent._send_via_rusender", return_value={"message_id": "msg-1"})
    @patch("src.campaigns.connection_service.resolve_connection")
    def test_rusender_passes_html_override(self, resolve_mock, send_mock) -> None:
        resolve_mock.return_value = ResolvedConnection(
            id="conn-1",
            transport="rusender",
            email="sender@example.com",
            sender_name="Sender",
            secret="key",
            api_base_url="https://api.example.test",
        )

        result = _send_delivery_message(
            connection_id="conn-1",
            owner_username="user",
            to_email="to@example.com",
            subject="Test",
            html=self._chain_html(),
            text=self._chain_text(),
        )

        self.assertEqual(result, "rusender:msg-1")
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs["html_override"], self._chain_html())
        self.assertEqual(kwargs["body_override"], self._chain_text())

    @patch("src.generator.delivery.sender_agent._send_via_mailopost", return_value={"uuid": "msg-2"})
    @patch("src.campaigns.connection_service.resolve_connection")
    def test_mailopost_passes_html_override(self, resolve_mock, send_mock) -> None:
        resolve_mock.return_value = ResolvedConnection(
            id="conn-2",
            transport="mailopost",
            email="sender@example.com",
            sender_name="Sender",
            secret="token",
            api_base_url="https://api.example.test",
        )

        result = _send_delivery_message(
            connection_id="conn-2",
            owner_username="user",
            to_email="to@example.com",
            subject="Test",
            html=self._chain_html(),
            text=self._chain_text(),
        )

        self.assertEqual(result, "mailopost:msg-2")
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs["html_override"], self._chain_html())
        self.assertEqual(kwargs["body_override"], self._chain_text())


if __name__ == "__main__":
    unittest.main()
