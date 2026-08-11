from __future__ import annotations

import unittest
from unittest.mock import patch

from src.campaigns.batch_worker import _send_delivery_message
from src.campaigns.connection_service import ResolvedConnection
from src.campaigns.connection_sender_warmup_service import WarmupSuspendedByCampaign


class BatchWorkerDeliveryTests(unittest.TestCase):
    def _chain_html(self) -> str:
        return (
            '<p>Hello</p>'
            '<a href="http://localhost:8006/chain/branch/uuid-1" '
            'style="background:#236348">Получить</a>'
        )

    def _chain_text(self) -> str:
        return "Hello\n\nПолучить: http://localhost:8006/chain/branch/uuid-1"

    @patch("src.generator.delivery.channel_guard.wait_for_channel_send_slot")
    @patch("src.generator.delivery.sender_agent._send_via_rusender", return_value={"message_id": "msg-1"})
    @patch("src.campaigns.connection_service.resolve_connection")
    def test_rusender_passes_html_override(self, resolve_mock, send_mock, wait_mock) -> None:
        resolve_mock.return_value = ResolvedConnection(
            id="conn-1",
            transport="rusender",
            email="sender@example.com",
            sender_name="Sender",
            secret="",
            api_base_url="https://api.example.test",
            sending_key_id=42,
        )

        result = _send_delivery_message(
            connection_id="conn-1",
            owner_username="user",
            to_email="to@example.com",
            subject="Test",
            html=self._chain_html(),
            text=self._chain_text(),
        )

        self.assertEqual(result, "msg-1")
        wait_mock.assert_called_once_with("conn-1", allow_warmup=False)
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs["html_override"], self._chain_html())
        self.assertEqual(kwargs["body_override"], self._chain_text())
        self.assertEqual(kwargs["credential_sending_key_id"], 42)
        self.assertNotIn("credential_api_key", kwargs)

    @patch("src.generator.delivery.channel_guard.wait_for_channel_send_slot")
    @patch("src.campaigns.connection_service.resolve_connection")
    @patch(
        "src.campaigns.connection_sender_warmup_service.active_rusender_campaigns_for_connection",
        return_value=[{"id": "campaign-1", "name": "Campaign"}],
    )
    def test_rusender_warmup_yields_to_running_campaign(
        self,
        campaign_mock,
        resolve_mock,
        wait_mock,
    ) -> None:
        resolve_mock.return_value = ResolvedConnection(
            id="conn-1",
            transport="rusender",
            email="sender@example.com",
            sender_name="Sender",
            secret="",
            api_base_url="https://api.example.test",
            sending_key_id=42,
        )

        with self.assertRaises(WarmupSuspendedByCampaign):
            _send_delivery_message(
                connection_id="conn-1",
                owner_username="user",
                to_email="to@example.com",
                subject="Warmup",
                html="<p>Warmup</p>",
                text="Warmup",
                send_mode="connection_warmup",
            )

        wait_mock.assert_not_called()
        campaign_mock.assert_called_once_with("conn-1")
        resolve_mock.assert_not_called()

    @patch("src.generator.delivery.channel_guard.wait_for_channel_send_slot")
    @patch("src.campaigns.connection_service.resolve_connection")
    @patch(
        "src.campaigns.connection_sender_warmup_service.active_rusender_campaigns_for_connection",
        side_effect=[[], [{"id": "campaign-1", "name": "Campaign"}]],
    )
    def test_rusender_warmup_rechecks_campaign_after_waiting_for_slot(
        self,
        campaign_mock,
        resolve_mock,
        wait_mock,
    ) -> None:
        resolve_mock.return_value = ResolvedConnection(
            id="conn-1",
            transport="rusender",
            email="sender@example.com",
            sender_name="Sender",
            secret="",
            api_base_url="https://api.example.test",
            sending_key_id=42,
        )

        with self.assertRaises(WarmupSuspendedByCampaign):
            _send_delivery_message(
                connection_id="conn-1",
                owner_username="user",
                to_email="to@example.com",
                subject="Warmup",
                html="<p>Warmup</p>",
                text="Warmup",
                send_mode="connection_warmup",
            )

        wait_mock.assert_called_once_with("conn-1", allow_warmup=True)
        self.assertEqual(campaign_mock.call_count, 2)

    @patch("src.generator.delivery.channel_guard.wait_for_channel_send_slot")
    @patch("src.generator.delivery.sender_agent._send_via_mailopost", return_value={"uuid": "msg-2"})
    @patch("src.campaigns.connection_service.resolve_connection")
    def test_mailopost_passes_html_override(self, resolve_mock, send_mock, wait_mock) -> None:
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

        self.assertEqual(result, "msg-2")
        wait_mock.assert_called_once_with("conn-2", allow_warmup=False)
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs["html_override"], self._chain_html())
        self.assertEqual(kwargs["body_override"], self._chain_text())


if __name__ == "__main__":
    unittest.main()
