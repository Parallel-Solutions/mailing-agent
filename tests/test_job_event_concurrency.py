from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import EventStreamCounter, JobEvent
from src.jobs.job_docs import append_event, replace_events
from tests.bootstrap import bootstrap_test_runtime


class JobEventConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)

    def test_parallel_appends_get_unique_contiguous_sequences(self) -> None:
        total = 40
        with ThreadPoolExecutor(max_workers=12) as pool:
            sequences = list(
                pool.map(
                    lambda index: append_event(
                        "job-race",
                        "race-stream",
                        {"index": index},
                    ),
                    range(total),
                )
            )

        self.assertEqual(sorted(sequences), list(range(1, total + 1)))
        with session_scope() as session:
            rows = session.execute(
                select(JobEvent)
                .where(
                    JobEvent.job_id == "job-race",
                    JobEvent.stream == "race-stream",
                )
                .order_by(JobEvent.seq.asc())
            ).scalars().all()
            counter = session.get(
                EventStreamCounter,
                {"job_id": "job-race", "stream": "race-stream"},
            )

        self.assertEqual([row.seq for row in rows], list(range(1, total + 1)))
        self.assertIsNotNone(counter)
        self.assertEqual(counter.last_seq, total)

    def test_idempotency_key_survives_parallel_retries(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            sequences = list(
                pool.map(
                    lambda _: append_event(
                        "job-idempotent",
                        "provider-events",
                        {"event": "delivered"},
                        idempotency_key="provider:event-42",
                    ),
                    range(16),
                )
            )

        self.assertEqual([value for value in sequences if value is not None], [1])
        with session_scope() as session:
            rows = session.execute(
                select(JobEvent).where(
                    JobEvent.job_id == "job-idempotent",
                    JobEvent.stream == "provider-events",
                )
            ).scalars().all()
        self.assertEqual(len(rows), 1)

    def test_replace_resets_counter_in_same_transaction(self) -> None:
        append_event("job-replace", "items", {"value": "old"})
        replace_events(
            "job-replace",
            "items",
            [{"value": "first"}, {"value": "second"}],
        )
        sequence = append_event("job-replace", "items", {"value": "third"})

        self.assertEqual(sequence, 3)


if __name__ == "__main__":
    unittest.main()
