from __future__ import annotations

import unittest
from uuid import uuid4

from tests.bootstrap import reset_test_database


class TaskQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def test_enqueue_deduplicates_active_job_task(self) -> None:
        from src.workers.task_queue import enqueue_task, get_queue_snapshot

        job_id = f"job-queue-{uuid4().hex[:8]}"
        first, created_first = enqueue_task(
            task_type="sender",
            job_id=job_id,
            owner_username="alice",
            payload={"kwargs": {"dry_run": True}},
        )
        second, created_second = enqueue_task(
            task_type="sender",
            job_id=job_id,
            owner_username="alice",
            payload={"kwargs": {"dry_run": True}},
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        snapshot = get_queue_snapshot(task_type="sender", job_id=job_id)
        self.assertEqual(snapshot["total_active"], 1)

    def test_claim_next_task_fifo(self) -> None:
        from src.workers.task_queue import claim_next_task, complete_task, enqueue_task

        job_a = f"job-a-{uuid4().hex[:8]}"
        job_b = f"job-b-{uuid4().hex[:8]}"
        task_a, _ = enqueue_task(task_type="sender", job_id=job_a, owner_username="alice", payload={})
        task_b, _ = enqueue_task(task_type="sender", job_id=job_b, owner_username="bob", payload={})
        claimed = claim_next_task(task_type="sender", worker_id="worker-1", lease_seconds=120)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.id, task_a.id)
        complete_task(claimed.id, worker_id="worker-1")
        claimed_b = claim_next_task(task_type="sender", worker_id="worker-1", lease_seconds=120)
        self.assertIsNotNone(claimed_b)
        assert claimed_b is not None
        self.assertEqual(claimed_b.id, task_b.id)


if __name__ == "__main__":
    unittest.main()
