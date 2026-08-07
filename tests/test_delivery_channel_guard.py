from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import select

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
            "sending_key_id": 123,
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

    def test_rusender_connections_with_same_key_share_error_rate(self) -> None:
        from src.infra.models import SmtpMailbox

        primary = self._connection(email="primary@example.com")
        secondary = self._connection(email="secondary@example.com")
        record_channel_outcome(
            connection_id=primary["id"],
            provider_message_id="shared-ok-1",
            provider_status="delivered",
        )
        record_channel_outcome(
            connection_id=secondary["id"],
            provider_message_id="shared-ok-2",
            provider_status="delivered",
        )
        snapshot = record_channel_outcome(
            connection_id=secondary["id"],
            provider_message_id="shared-error",
            provider_status="hard_bounced",
        )

        self.assertEqual(snapshot["terminal_count"], 3)
        self.assertEqual(snapshot["error_count"], 1)
        self.assertEqual(snapshot["state"], "throttled")
        with session_scope() as session:
            self.assertEqual(
                session.get(SmtpMailbox, primary["id"]).delivery_guard_state,
                "throttled",
            )
            self.assertEqual(
                session.get(SmtpMailbox, secondary["id"]).delivery_guard_state,
                "throttled",
            )

    def test_error_rate_accumulates_beyond_the_old_sixty_minute_window(self) -> None:
        from src.infra.models import DeliveryKeyGuard

        connection = self._connection()
        with session_scope() as session:
            guard = session.scalar(select(DeliveryKeyGuard))
            guard.delivery_guard_monitoring_started_at = datetime.now(timezone.utc) - timedelta(days=1)

        occurred_at = datetime.now(timezone.utc) - timedelta(hours=2)
        for index in range(2):
            record_channel_outcome(
                connection_id=connection["id"],
                provider_message_id=f"old-ok-{index}",
                provider_status="delivered",
                occurred_at=occurred_at,
            )
        snapshot = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="old-error",
            provider_status="hard_bounced",
            occurred_at=occurred_at,
        )

        self.assertEqual(snapshot["tracking_mode"], "since_reset")
        self.assertEqual(snapshot["terminal_count"], 3)
        self.assertEqual(snapshot["error_count"], 1)
        self.assertEqual(snapshot["state"], "throttled")

    def test_exactly_five_percent_does_not_trigger_but_more_than_five_does(self) -> None:
        connection = self._connection(delivery_error_min_samples=20)
        for index in range(19):
            record_channel_outcome(
                connection_id=connection["id"],
                provider_message_id=f"threshold-ok-{index}",
                provider_status="delivered",
            )

        exact_threshold = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="threshold-error-1",
            provider_status="hard_bounced",
        )
        self.assertEqual(exact_threshold["error_rate"], 0.05)
        self.assertEqual(exact_threshold["state"], "normal")

        above_threshold = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="threshold-error-2",
            provider_status="hard_bounced",
        )
        self.assertGreater(above_threshold["error_rate"], 0.05)
        self.assertEqual(above_threshold["state"], "throttled")

    def test_provider_acceptance_is_pending_until_final_webhook(self) -> None:
        connection = self._connection(delivery_error_min_samples=1)

        accepted = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="pending-message",
            provider_status="accepted",
        )
        self.assertEqual(accepted["terminal_count"], 0)
        self.assertEqual(accepted["state"], "normal")

        delivered = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="pending-message",
            provider_status="delivered",
        )
        self.assertEqual(delivered["terminal_count"], 1)
        self.assertEqual(delivered["error_count"], 0)

    def test_critical_error_count_no_longer_triggers_guard(self) -> None:
        connection = self._connection(
            delivery_error_rate_threshold=1.0,
            delivery_error_min_samples=20,
            delivery_error_critical_count=1,
        )
        snapshot = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="single-error",
            provider_status="hard_bounced",
        )

        self.assertEqual(snapshot["error_rate"], 1.0)
        self.assertEqual(snapshot["state"], "normal")

    def test_older_webhook_cannot_overwrite_newer_final_status(self) -> None:
        from src.infra.models import DeliveryChannelOutcome, DeliveryKeyGuard

        connection = self._connection(delivery_error_min_samples=1)
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            guard = session.scalar(select(DeliveryKeyGuard))
            guard.delivery_guard_monitoring_started_at = now - timedelta(hours=2)

        record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="out-of-order",
            provider_status="delivered",
            occurred_at=now,
        )
        snapshot = record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="out-of-order",
            provider_status="hard_bounced",
            occurred_at=now - timedelta(hours=1),
        )

        self.assertEqual(snapshot["terminal_count"], 1)
        self.assertEqual(snapshot["error_count"], 0)
        self.assertEqual(snapshot["state"], "normal")
        with session_scope() as session:
            outcome = session.scalar(
                select(DeliveryChannelOutcome).where(
                    DeliveryChannelOutcome.provider_message_id == "out-of-order"
                )
            )
            self.assertEqual(outcome.provider_status, "delivered")
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
        secondary = self._connection(
            email="secondary-disable@example.com",
            delivery_error_min_samples=1,
            delivery_error_critical_count=1,
            delivery_error_action="disable",
        )
        campaign = create_campaign(
            self.owner,
            {
                "name": "Guard pause campaign",
                "smtp_mailbox_id": secondary["id"],
                "connection_ids": [secondary["id"]],
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
        with self.assertRaises(DeliveryChannelDisabled):
            reserve_channel_send_slot(secondary["id"])

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


    def test_failed_key_warmup_keeps_key_blocked_and_preserves_outcomes(self) -> None:
        from src.generator.delivery.connection_warmup import run_connection_warmup
        from src.infra.models import (
            BackgroundTask,
            DeliveryChannelOutcome,
            DeliveryKeyGuard,
            SmtpMailbox,
        )

        connection = self._connection(
            delivery_error_min_samples=1,
            delivery_error_action="warmup",
            warmup_recipients=["warmup@example.com"],
        )
        record_channel_outcome(
            connection_id=connection["id"],
            provider_message_id="failed-before-warmup",
            provider_status="hard_bounced",
        )
        with session_scope() as session:
            mailbox = session.get(SmtpMailbox, connection["id"])
            task = session.get(BackgroundTask, mailbox.warmup_task_id)
            payload = dict(task.payload)
            key_guard_id = payload["key_guard_id"]

        with patch(
            "src.campaigns.batch_worker._send_delivery_message",
            side_effect=RuntimeError("provider rejected warmup"),
        ):
            result = run_connection_warmup(payload)

        self.assertEqual(result["status"], "failed")
        with session_scope() as session:
            guard = session.get(DeliveryKeyGuard, key_guard_id)
            self.assertEqual(guard.delivery_guard_state, "warmup")
            self.assertEqual(guard.warmup_status, "failed")
            preserved = session.scalar(
                select(DeliveryChannelOutcome).where(
                    DeliveryChannelOutcome.delivery_key_guard_id == key_guard_id
                )
            )
            self.assertIsNotNone(preserved)

if __name__ == "__main__":
    unittest.main()
