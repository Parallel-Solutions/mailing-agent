from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import select

from src.campaigns.batch_worker import finalize_sender_batch_task_failure, run_sender_batch
from src.campaigns.connection_service import ResolvedConnection
from src.campaigns.service import (
    MAX_SENDER_BATCH_WORKER_RECOVERIES,
    create_campaign,
    replace_recipients,
)
from src.infra.db import session_scope
from src.infra.models import BackgroundTask, Campaign, CampaignBatch, CampaignRecipient, CampaignSchedule
from src.security.user_store import create_user
from src.workers.task_queue import enqueue_task
from tests.bootstrap import bootstrap_test_runtime


class BatchWorkerOnErrorPauseTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"bwp{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "On error pause behaves as skip"})
        self.campaign_id = self.campaign["id"]
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[
                {"company": "A", "contact_name": "One", "email": "bad@example.com"},
                {"company": "B", "contact_name": "Two", "email": "good@example.com"},
            ],
        )
        with session_scope() as session:
            recipients = session.scalars(
                select(CampaignRecipient)
                .where(CampaignRecipient.campaign_id == self.campaign_id)
                .order_by(CampaignRecipient.row_index)
            ).all()
            self.recipient_ids = [int(r.id) for r in recipients]
            camp = session.get(Campaign, self.campaign_id)
            assert camp is not None
            camp.status = "running"
            camp.smtp_mailbox_id = "conn-test"
            self.batch_id = str(uuid.uuid4())
            session.add(
                CampaignBatch(
                    id=self.batch_id,
                    campaign_id=self.campaign_id,
                    batch_index=0,
                    scheduled_at=datetime.now(timezone.utc),
                    size=2,
                    status="pending",
                    recipient_ids=self.recipient_ids,
                )
            )
            session.flush()

    @patch("src.campaigns.batch_worker._load_email_template", return_value=("Subject", "<p>Hi</p>", "Hi"))
    @patch("src.campaigns.batch_worker._send_delivery_message")
    @patch("src.campaigns.connection_service.pick_available_connection")
    @patch("src.campaigns.recipient_email_service.resolve_delivery_email")
    def test_on_error_pause_continues_batch(
        self,
        resolve_mock,
        pick_mock,
        send_mock,
        _template_mock,
    ) -> None:
        pick_mock.return_value = ResolvedConnection(
            id="conn-test",
            transport="smtp",
            email="sender@example.com",
            sender_name="Sender",
            secret="secret",
            api_base_url="",
        )

        def resolve_side_effect(recipient, **kwargs):
            email = str(recipient.email or "")
            if email.startswith("bad@"):
                return None, []
            return email, []

        resolve_mock.side_effect = resolve_side_effect
        send_mock.return_value = "smtp:good@example.com:1"

        result = run_sender_batch(
            {
                "campaign_id": self.campaign_id,
                "batch_id": self.batch_id,
                "send_mode": "email",
                "connection_ids": ["conn-test"],
                "on_error": "pause",
            }
        )

        self.assertEqual(result.get("sent"), 1)
        self.assertEqual(result.get("errors"), 1)
        with session_scope() as session:
            camp = session.get(Campaign, self.campaign_id)
            assert camp is not None
            self.assertEqual(camp.status, "completed_with_errors")
            batch = session.get(CampaignBatch, self.batch_id)
            assert batch is not None
            self.assertIn(batch.status, {"completed", "completed_with_errors"})


class FinalizeSenderBatchRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"bwr{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Worker recovery"})
        self.campaign_id = self.campaign["id"]
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[{"company": "A", "contact_name": "One", "email": "one@example.com"}],
        )
        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
            assert recipient is not None
            self.recipient_id = int(recipient.id)
            camp = session.get(Campaign, self.campaign_id)
            assert camp is not None
            camp.status = "running"
            self.batch_id = str(uuid.uuid4())
            session.add(
                CampaignBatch(
                    id=self.batch_id,
                    campaign_id=self.campaign_id,
                    batch_index=0,
                    scheduled_at=datetime.now(timezone.utc),
                    size=1,
                    status="running",
                    recipient_ids=[self.recipient_id],
                )
            )
            task, _ = enqueue_task(
                task_type="sender_batch",
                job_id=self.campaign_id,
                owner_username=self.username,
                payload={"campaign_id": self.campaign_id, "batch_id": self.batch_id},
            )
            self.task_id = str(task["id"])
            batch = session.get(CampaignBatch, self.batch_id)
            assert batch is not None
            batch.task_id = self.task_id
            session.flush()

    @patch("src.workers.task_queue.enqueue_task")
    def test_recovery_re_enqueues_running_campaign(self, enqueue_mock) -> None:
        enqueue_mock.return_value = ({"id": "new-task-id"}, True)

        finalize_sender_batch_task_failure(self.task_id, "worker process exited with code 1")

        enqueue_mock.assert_called_once()
        with session_scope() as session:
            camp = session.get(Campaign, self.campaign_id)
            batch = session.get(CampaignBatch, self.batch_id)
            assert camp is not None
            assert batch is not None
            self.assertEqual(camp.status, "running")
            self.assertEqual(batch.status, "pending")
            self.assertEqual(batch.worker_recovery_count, 1)
            self.assertEqual(batch.task_id, "new-task-id")

    @patch("src.workers.task_queue.enqueue_task")
    def test_recovery_skips_enqueue_when_campaign_paused(self, enqueue_mock) -> None:
        with session_scope() as session:
            camp = session.get(Campaign, self.campaign_id)
            assert camp is not None
            camp.status = "paused"
            session.flush()

        finalize_sender_batch_task_failure(self.task_id, "worker crashed")

        enqueue_mock.assert_not_called()
        with session_scope() as session:
            camp = session.get(Campaign, self.campaign_id)
            batch = session.get(CampaignBatch, self.batch_id)
            assert camp is not None
            assert batch is not None
            self.assertEqual(camp.status, "paused")
            self.assertEqual(batch.status, "pending")
            self.assertEqual(batch.worker_recovery_count, 1)

    @patch("src.workers.task_queue.enqueue_task")
    def test_recovery_exhausted_does_not_pause_campaign(self, enqueue_mock) -> None:
        with session_scope() as session:
            batch = session.get(CampaignBatch, self.batch_id)
            assert batch is not None
            batch.worker_recovery_count = MAX_SENDER_BATCH_WORKER_RECOVERIES
            session.flush()

        finalize_sender_batch_task_failure(self.task_id, "worker crashed again")

        enqueue_mock.assert_not_called()
        with session_scope() as session:
            camp = session.get(Campaign, self.campaign_id)
            batch = session.get(CampaignBatch, self.batch_id)
            assert camp is not None
            assert batch is not None
            self.assertEqual(batch.status, "failed")
            self.assertEqual(camp.status, "completed_with_errors")
            recipient = session.get(CampaignRecipient, self.recipient_id)
            assert recipient is not None
            self.assertEqual(recipient.send_status, "failed")


if __name__ == "__main__":
    unittest.main()
