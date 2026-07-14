from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.infra.db import session_scope
from src.infra.models import AgentState, BackgroundTask
from src.workers.task_queue import (
    CANCELLED,
    COMPLETED,
    FAILED,
    RETRY,
    claim_task,
    complete_task,
    enqueue_task,
    fail_task,
    get_task,
    heartbeat_task,
    recover_expired_tasks,
    reconcile_orphaned_agent_states,
    request_cancel,
)
from tests.bootstrap import bootstrap_test_runtime


class DurableTaskQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)

    def test_enqueue_deduplicates_active_job_task(self) -> None:
        first, first_created = enqueue_task(
            task_type="documents",
            job_id="job-queue",
            payload={"mode": "fast"},
        )
        second, second_created = enqueue_task(
            task_type="documents",
            job_id="job-queue",
            payload={"mode": "quality"},
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], second["id"])

    def test_claim_heartbeat_and_complete(self) -> None:
        queued, _ = enqueue_task(task_type="sender", job_id="job-send")
        claimed = claim_task(worker_id="worker-a", lease_seconds=60)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], queued["id"])
        self.assertEqual(claimed["attempt"], 1)
        self.assertTrue(
            heartbeat_task(
                task_id=claimed["id"],
                worker_id="worker-a",
                lease_seconds=60,
            )
        )
        self.assertTrue(
            complete_task(
                task_id=claimed["id"],
                worker_id="worker-a",
                result={"sent": 3},
            )
        )
        completed = get_task(claimed["id"])
        self.assertEqual(completed["status"], COMPLETED)
        self.assertEqual(completed["result"], {"sent": 3})

    def test_expired_lease_is_requeued_and_claimed_again(self) -> None:
        queued, _ = enqueue_task(
            task_type="parser_start",
            job_id="job-parser",
            max_attempts=3,
        )
        claimed = claim_task(worker_id="worker-a", lease_seconds=60)
        with session_scope() as session:
            row = session.get(BackgroundTask, claimed["id"])
            row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        self.assertEqual(recover_expired_tasks(), 1)
        recovered = get_task(queued["id"])
        self.assertEqual(recovered["status"], RETRY)

        reclaimed = claim_task(worker_id="worker-b", lease_seconds=60)
        self.assertEqual(reclaimed["id"], queued["id"])
        self.assertEqual(reclaimed["attempt"], 2)

    def test_failure_retries_then_becomes_terminal(self) -> None:
        queued, _ = enqueue_task(
            task_type="documents",
            job_id="job-fail",
            max_attempts=2,
        )
        first = claim_task(worker_id="worker-a", lease_seconds=60)
        status = fail_task(
            task_id=first["id"],
            worker_id="worker-a",
            error="first failure",
            retry_base_seconds=1,
        )
        self.assertEqual(status, RETRY)

        with session_scope() as session:
            row = session.get(BackgroundTask, queued["id"])
            row.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        second = claim_task(worker_id="worker-b", lease_seconds=60)
        status = fail_task(
            task_id=second["id"],
            worker_id="worker-b",
            error="second failure",
            retry_base_seconds=1,
        )
        self.assertEqual(status, FAILED)
        self.assertEqual(get_task(queued["id"])["status"], FAILED)

    def test_cancelled_queued_task_is_never_claimed(self) -> None:
        queued, _ = enqueue_task(task_type="sender", job_id="job-cancel")
        cancelled = request_cancel(queued["id"])

        self.assertEqual(cancelled["status"], CANCELLED)

    def test_orphaned_running_state_is_reconciled_after_restart(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        with session_scope() as session:
            session.add(
                AgentState(
                    job_id="job-orphan",
                    agent_name="sender",
                    state={"status": "running"},
                    details=None,
                    updated_at=old,
                )
            )

        self.assertEqual(reconcile_orphaned_agent_states(grace_seconds=0), 1)
        with session_scope() as session:
            row = session.get(
                AgentState,
                {"job_id": "job-orphan", "agent_name": "sender"},
            )
        self.assertEqual(row.state["status"], "error")
        self.assertTrue(row.state["recovered_after_restart"])
        self.assertIsNone(claim_task(worker_id="worker-a", lease_seconds=60))

    def test_sender_queue_snapshot_tracks_position(self) -> None:
        from uuid import uuid4

        from src.workers.task_queue import get_queue_snapshot

        job_a = f"job-a-{uuid4().hex[:8]}"
        job_b = f"job-b-{uuid4().hex[:8]}"
        enqueue_task(task_type="sender", job_id=job_a, owner_username="alice", payload={"kwargs": {}})
        enqueue_task(task_type="sender", job_id=job_b, owner_username="bob", payload={"kwargs": {}})
        snapshot = get_queue_snapshot(task_type="sender", job_id=job_b)
        self.assertEqual(snapshot["total_active"], 2)
        self.assertEqual(snapshot["job_queue_position"], 2)


if __name__ == "__main__":
    unittest.main()
