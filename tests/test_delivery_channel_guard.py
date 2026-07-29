from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet

from src.campaigns.connection_service import create_connection
from src.campaigns.service import create_campaign
from src.generator.delivery.channel_guard import (
    DeliveryChannelDisabled,
    normalize_guard_settings,
    record_channel_outcome,
    reserve_channel_send_slot,
    reset_channel_guard,
)
from src.generator.delivery.suppression_store import reason_from_delivery_response
from src.infra.db import session_scope
from src.infra.models import Campaign
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class DeliveryResponseClassificationTests(unittest.TestCase):
    def test_warmup_settings_are_normalized(self) -> None:
        normalized = normalize_guard_settings(
            {
                "delivery_error_action": "warmup",
                "warmup_recipients": [
                    " First@Example.com ",
                    "first@example.com",
                    "second@example.com",
                ],
                "warmup_percent_of_errors": 150,
            }
        )
        self.assertEqual(normalized["delivery_error_action"], "warmup")
        self.assertEqual(
            normalized["warmup_recipients"],
            ["first@example.com", "second@example.com"],
        )
        self.assertEqual(normalized["warmup_percent_of_errors"], 150)

    def test_invalid_warmup_recipient_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Некорректный адрес"):
            normalize_guard_settings({"warmup_recipients": ["not-an-email"]})

    def test_unknown_recipient_is_hard_bounce(self) -> None:
        self.assertEqual(
            reason_from_delivery_response("550 5.1.1 user unknown"),
            "hard_bounce",
        )

    def test_mailbox_full_is_soft_bounce(self) -> None:
        self.assertEqual(
            reason_from_delivery_response("452 4.2.2 mailbox full"),
            "soft_bounce",
        )

    def test_sender_policy_error_does_not_suppress_recipient(self) -> None:
        self.assertIsNone(reason_from_delivery_response("550 SPF policy rejected sender"))


class DeliveryChannelGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.owner = f"guard-{uuid.uuid4().hex[:8]}"
        create_user(self.owner, "Pass12345!")

    def _connection(self, **overrides):
        payload = {
            "transport": "rusender",
            "email": "verified@example.com",
            "api_token": "rs_guard_secret",
            "delivery_error_rate_threshold": 0.05,
            "delivery_error_min_samples": 3,
            "delivery_error_critical_count": 0,
            "delivery_error_action": "throttle",
            "delivery_throttled_max_per_hour": 50,
            "delivery_guard_enabled": True,
        }
        payload.update(overrides)
        return create_connection(self.owner, payload)

    def test_error_rate_throttles_entire_connection(self) -> None:
        connection = self._connection()
        for index in range(2):
            snapshot = record_channel_outcome(
                connection_id=connection["id"],
                provider_message_id=f"ok-{index}",
                provider_status="delivered",
            )
            self.assertEqual(snapshot["state"], "normal")
        snapshot = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="failed-1",
            provider_status="hard_bounced",
        )
        self.assertEqual(snapshot["state"], "throttled")
        self.assertEqual(snapshot["effective_max_per_hour"], 50)
        self.assertEqual(snapshot["terminal_count"], 3)
        self.assertEqual(snapshot["error_count"], 1)

    def test_shared_slot_limit_is_persisted_in_database(self) -> None:
        connection = self._connection(max_per_hour=2)
        self.assertEqual(reserve_channel_send_slot(connection["id"]), 0.0)
        self.assertEqual(reserve_channel_send_slot(connection["id"]), 0.0)
        self.assertGreater(reserve_channel_send_slot(connection["id"]), 0.0)

    def test_disable_action_pauses_campaigns_using_connection(self) -> None:
        connection = self._connection(
            delivery_error_min_samples=1,
            delivery_error_critical_count=1,
            delivery_error_action="disable",
        )
        campaign = create_campaign(
            self.owner,
            {
                "name": "Guard pause campaign",
                "smtp_mailbox_id": connection["id"],
                "connection_ids": [connection["id"]],
                "transport": "rusender",
            },
        )
        with session_scope() as session:
            row = session.get(Campaign, campaign["id"])
            row.status = "running"

        snapshot = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="failed-disable",
            provider_status="hard_bounced",
        )
        self.assertEqual(snapshot["state"], "disabled")
        self.assertEqual(snapshot["paused_campaigns"], 1)
        with session_scope() as session:
            self.assertEqual(session.get(Campaign, campaign["id"]).status, "paused")
        with self.assertRaises(DeliveryChannelDisabled):
            reserve_channel_send_slot(connection["id"])

        reset = reset_channel_guard(connection["id"])
        self.assertEqual(reset["state"], "normal")
        with session_scope() as session:
            self.assertEqual(session.get(Campaign, campaign["id"]).status, "paused")

    def test_warmup_action_pauses_campaign_and_enqueues_warmup(self) -> None:
        from src.infra.models import BackgroundTask, SmtpMailbox

        connection = self._connection(
            delivery_error_min_samples=1,
            delivery_error_action="warmup",
            warmup_recipients=["warmup@example.com"],
            warmup_percent_of_errors=200,
        )
        campaign = create_campaign(
            self.owner,
            {
                "name": "Warmup pause campaign",
                "connection_ids": [connection["id"]],
                "transport": "rusender",
            },
        )
        with session_scope() as session:
            session.get(Campaign, campaign["id"]).status = "running"

        snapshot = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="failed-warmup",
            provider_status="hard_bounced",
        )

        self.assertEqual(snapshot["state"], "warmup")
        self.assertEqual(snapshot["paused_campaigns"], 1)
        self.assertEqual(snapshot["warmup_status"], "queued")
        with self.assertRaises(DeliveryChannelDisabled):
            reserve_channel_send_slot(connection["id"])
        self.assertEqual(
            reserve_channel_send_slot(connection["id"], allow_warmup=True),
            0.0,
        )
        with session_scope() as session:
            self.assertEqual(session.get(Campaign, campaign["id"]).status, "paused")
            mailbox = session.get(SmtpMailbox, connection["id"])
            self.assertEqual(mailbox.warmup_status, "queued")
            task = session.get(BackgroundTask, mailbox.warmup_task_id)
            self.assertEqual(task.task_type, "connection_warmup")
            self.assertEqual(task.payload["message_count"], 2)


if __name__ == "__main__":
    unittest.main()
