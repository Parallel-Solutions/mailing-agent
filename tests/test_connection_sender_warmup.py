from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select

from src.campaigns import connection_sender_warmup_service as warmup
from src.infra.db import session_scope
from src.infra.models import (
    ConnectionWarmupDelivery,
    ConnectionWarmupProgram,
    SmtpMailbox,
)
from tests.bootstrap import bootstrap_test_runtime


class ConnectionSenderWarmupTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.owner = f"warmup-{uuid4().hex[:8]}"
        self.connection_id = str(uuid4())
        self.smtp_connection_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            session.add(SmtpMailbox(
                id=self.connection_id,
                owner_username=self.owner,
                provider="rusender",
                email="sender@example.com",
                sender_name="Sender",
                host="https://api.rusender.ru/api/v1",
                port=443,
                use_ssl=True,
                use_starttls=False,
                auth_method="environment",
                password_encrypted="",
                sending_key_id=123,
                status="active",
                is_default=True,
                created_at=now,
                updated_at=now,
            ))
            session.add(SmtpMailbox(
                id=self.smtp_connection_id,
                owner_username=self.owner,
                provider="rusender",
                email="alternate-sender@example.com",
                sender_name="Sender",
                host="https://api.rusender.ru/api/v1",
                port=443,
                use_ssl=True,
                use_starttls=False,
                auth_method="environment",
                password_encrypted="",
                sending_key_id=123,
                status="active",
                is_default=False,
                created_at=now,
                updated_at=now,
            ))
        self.visibility = frozenset({self.owner})
        warmup.update_program(
            self.connection_id,
            self.owner,
            {"smtp_connection_id": self.smtp_connection_id},
            visible_owners=self.visibility,
        )

    def test_recipient_list_is_deduplicated_and_classified(self) -> None:
        result = warmup.add_recipients(
            self.connection_id,
            self.owner,
            [" First@Gmail.com ", "first@gmail.com", "person@yandex.ru"],
            visible_owners=self.visibility,
        )
        self.assertEqual(result["recipient_count"], 2)
        self.assertEqual(result["active_recipient_count"], 2)
        providers = {item["email"]: item["provider"] for item in result["recipients"]}
        self.assertEqual(providers["first@gmail.com"], "gmail")
        self.assertEqual(providers["person@yandex.ru"], "yandex")
        self.assertEqual(result["effective_daily_plan"][0], 5)

    def test_rusender_key_can_use_any_sender_from_same_key(self) -> None:
        result = warmup.update_program(
            self.connection_id,
            self.owner,
            {"smtp_connection_id": self.connection_id},
            visible_owners=self.visibility,
        )
        self.assertEqual(result["smtp_connection_id"], self.connection_id)

    def test_recipient_can_be_disabled_and_reactivated_by_readding(self) -> None:
        result = warmup.add_recipients(
            self.connection_id,
            self.owner,
            ["person@mail.ru"],
            visible_owners=self.visibility,
        )
        recipient_id = result["recipients"][0]["id"]
        result = warmup.set_recipient_status(
            self.connection_id,
            recipient_id,
            self.owner,
            "disabled",
            visible_owners=self.visibility,
        )
        self.assertEqual(result["active_recipient_count"], 0)
        result = warmup.add_recipients(
            self.connection_id,
            self.owner,
            ["person@mail.ru"],
            visible_owners=self.visibility,
        )
        self.assertEqual(result["active_recipient_count"], 1)

    def test_diagnostics_blocks_missing_authentication_records(self) -> None:
        with patch.object(warmup, "_txt_records", return_value=[]):
            result = warmup.run_diagnostics(
                self.connection_id,
                self.owner,
                visible_owners=self.visibility,
            )
        self.assertEqual(result["diagnostics_status"], "blocked")
        self.assertEqual(
            [item["status"] for item in result["diagnostics"]["checks"][1:3]],
            ["fail", "fail"],
        )

    def test_daily_run_schedules_messages_and_advances_after_sends(self) -> None:
        warmup.add_recipients(
            self.connection_id,
            self.owner,
            ["one@gmail.com", "two@outlook.com"],
            visible_owners=self.visibility,
        )
        with patch.object(
            warmup,
            "_txt_records",
            side_effect=lambda name: ["v=DMARC1; p=none"] if name.startswith("_dmarc") else ["v=spf1 include:example.net ~all"],
        ):
            warmup.run_diagnostics(
                self.connection_id,
                self.owner,
                visible_owners=self.visibility,
            )
        warmup.update_program(
            self.connection_id,
            self.owner,
            {"recipients_consent_confirmed": True},
            visible_owners=self.visibility,
        )
        with patch.object(warmup, "_enqueue_day", return_value="task-start"):
            program = warmup.start_program(
                self.connection_id,
                self.owner,
                visible_owners=self.visibility,
            )
        with patch.object(warmup, "_enqueue_delivery", return_value="message-task") as enqueue_delivery:
            result = warmup.run_warmup_day({"program_id": program["id"]})
        self.assertEqual(result["scheduled"], 5)
        self.assertEqual(enqueue_delivery.call_count, 5)

        with session_scope() as session:
            deliveries = session.execute(
                select(ConnectionWarmupDelivery).order_by(ConnectionWarmupDelivery.created_at.asc())
            ).scalars().all()
            delivery_ids = [item.id for item in deliveries]
            self.assertEqual(len(delivery_ids), 5)
            distribution = Counter(item.recipient_id for item in deliveries)
            self.assertEqual(sorted(distribution.values()), [2, 3])

        with (
            patch("src.campaigns.batch_worker._send_delivery_message", side_effect=[f"message-{index}" for index in range(1, 6)]) as send,
            patch.object(warmup, "_enqueue_day", return_value="task-next") as enqueue_day,
        ):
            for delivery_id in delivery_ids:
                warmup.run_warmup_message({"delivery_id": delivery_id})
        self.assertEqual(send.call_count, 5)
        self.assertTrue(all(call.kwargs["connection_id"] == self.smtp_connection_id for call in send.call_args_list))
        self.assertTrue(all(call.kwargs["send_mode"] == "connection_warmup" for call in send.call_args_list))
        enqueue_day.assert_called_once_with(program["id"], immediate=False)
        with session_scope() as session:
            current = session.get(ConnectionWarmupProgram, program["id"])
            self.assertEqual(current.current_day, 2)
            self.assertEqual(current.status, "running")

    def test_hard_bounce_disables_recipient_and_pauses_program(self) -> None:
        result = warmup.add_recipients(
            self.connection_id,
            self.owner,
            ["bounce@gmail.com"],
            visible_owners=self.visibility,
        )
        recipient_id = result["recipients"][0]["id"]
        program_id = result["id"]
        with session_scope() as session:
            program = session.get(ConnectionWarmupProgram, program_id)
            program.status = "running"
            delivery = ConnectionWarmupDelivery(
                id=str(uuid4()),
                program_id=program_id,
                recipient_id=recipient_id,
                day_number=1,
                status="accepted",
                provider_message_id="provider-1",
                scheduled_at=datetime.now(timezone.utc),
                sent_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(delivery)
        outcome = warmup.record_warmup_delivery_outcome(
            provider_message_id="provider-1",
            provider_status="hard_bounced",
            smtp_response="550 user unknown",
        )
        self.assertTrue(outcome["paused"])
        with session_scope() as session:
            program = session.get(ConnectionWarmupProgram, program_id)
            recipient = session.get(warmup.ConnectionWarmupRecipient, recipient_id)
            self.assertEqual(program.status, "paused")
            self.assertEqual(recipient.status, "disabled")

    def test_provider_idempotency_key_can_match_a_warmup_delivery(self) -> None:
        from src.generator.delivery.sender_agent import _build_provider_idempotency_key

        result = warmup.add_recipients(
            self.connection_id,
            self.owner,
            ["alias@gmail.com"],
            visible_owners=self.visibility,
        )
        delivery_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            program = session.get(ConnectionWarmupProgram, result["id"])
            program.status = "running"
            session.add(ConnectionWarmupDelivery(
                id=delivery_id,
                program_id=result["id"],
                recipient_id=result["recipients"][0]["id"],
                day_number=1,
                run_number=1,
                status="accepted",
                provider_message_id="provider-uuid",
                scheduled_at=now,
                sent_at=now,
                created_at=now,
                updated_at=now,
            ))
        alias = _build_provider_idempotency_key(
            provider="rusender",
            job_id=None,
            row_id=f"sender-warmup-{delivery_id}",
            recipient="alias@gmail.com",
            send_mode="connection_warmup",
        )
        outcome = warmup.record_warmup_delivery_outcome(
            provider_message_id=alias,
            provider_status="delivered",
        )
        self.assertEqual(outcome["delivery_id"], delivery_id)
        self.assertEqual(outcome["status"], "delivered")
        self.assertFalse(outcome["paused"])

    def test_growth_setting_rebuilds_the_daily_plan(self) -> None:
        result = warmup.get_program(
            self.connection_id,
            self.owner,
            visible_owners=self.visibility,
        )
        self.assertEqual(result["daily_plan"], [5, 8, 10, 15, 19, 24, 25, 30, 38, 48, 50, 50, 50, 50])
        result = warmup.update_program(
            self.connection_id,
            self.owner,
            {"max_growth_percent": 20},
            visible_owners=self.visibility,
        )
        self.assertEqual(result["daily_plan"], [5, 8, 10, 15, 18, 22, 25, 30, 36, 44, 50, 50, 50, 50])

    def test_restart_uses_a_new_run_and_preserves_old_deliveries(self) -> None:
        result = warmup.add_recipients(
            self.connection_id,
            self.owner,
            ["repeat@gmail.com"],
            visible_owners=self.visibility,
        )
        program_id = result["id"]
        recipient_id = result["recipients"][0]["id"]
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            program = session.get(ConnectionWarmupProgram, program_id)
            program.status = "completed"
            program.diagnostics_status = "warning"
            program.recipients_consent_confirmed = True
            program.current_day = 15
            program.completed_at = now
            session.add(ConnectionWarmupDelivery(
                id=str(uuid4()),
                program_id=program_id,
                recipient_id=recipient_id,
                day_number=1,
                run_number=1,
                status="delivered",
                provider_message_id="old-message",
                scheduled_at=now,
                sent_at=now,
                created_at=now,
                updated_at=now,
            ))
        with patch.object(warmup, "_enqueue_day", return_value="new-run-task"):
            restarted = warmup.start_program(
                self.connection_id,
                self.owner,
                visible_owners=self.visibility,
            )
        self.assertEqual(restarted["run_number"], 2)
        with patch.object(warmup, "_enqueue_delivery", return_value="message-task"):
            scheduled = warmup.run_warmup_day({"program_id": program_id, "run_number": 2})
        self.assertEqual(scheduled["scheduled"], 5)
        with session_scope() as session:
            runs = session.execute(
                select(ConnectionWarmupDelivery.run_number).order_by(ConnectionWarmupDelivery.run_number.asc())
            ).scalars().all()
        self.assertEqual(runs, [1, 2, 2, 2, 2, 2])

if __name__ == "__main__":
    unittest.main()