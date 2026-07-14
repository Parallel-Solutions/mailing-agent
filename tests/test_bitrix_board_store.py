from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from src.bitrix_board.agent_runner import format_questions_message, parse_agent_json
from src.bitrix_board.states import TaskState
from src.bitrix_board.store import BoardStore


class BoardStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "state.db"
        self.store = BoardStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_create_run_fixes_snapshot_order(self) -> None:
        run = self.store.create_run(
            project="Demo",
            source_stage="Backlog",
            target_stage="Review",
            concurrency=3,
            snapshot_task_ids=[103, 101, 102],
            task_titles={101: "A", 102: "B", 103: "C"},
        )
        self.assertEqual(run.snapshot_task_ids, [103, 101, 102])
        tasks = self.store.list_tasks(run.id)
        self.assertEqual([task.task_id for task in tasks], [103, 101, 102])
        self.assertTrue(all(task.state == TaskState.QUEUED for task in tasks))

    def test_snapshot_does_not_include_new_tasks(self) -> None:
        run = self.store.create_run(
            project="Demo",
            source_stage="Backlog",
            target_stage="Review",
            concurrency=2,
            snapshot_task_ids=[1, 2],
            task_titles={1: "One", 2: "Two"},
        )
        now = time.time()
        self.store._conn.execute(
            """
            INSERT INTO board_tasks (
                run_id, task_id, title, state, position,
                review_cycle, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (run.id, 99, "Late task", TaskState.QUEUED.value, 99, now, now),
        )
        self.store._conn.commit()
        tasks = self.store.list_tasks(run.id)
        self.assertEqual(run.snapshot_task_ids, [1, 2])
        self.assertEqual(len([task for task in tasks if task.task_id in {1, 2}]), 2)

    def test_waiting_for_answer_does_not_use_slot(self) -> None:
        run = self.store.create_run(
            project="Demo",
            source_stage="Backlog",
            target_stage="Review",
            concurrency=2,
            snapshot_task_ids=[1, 2, 3],
            task_titles={1: "One", 2: "Two", 3: "Three"},
        )
        tasks = self.store.list_tasks(run.id)
        self.store.update_task(tasks[0].id, state=TaskState.PLANNING, agent_pid=111)
        self.store.update_task(tasks[1].id, state=TaskState.WAITING_FOR_ANSWER)
        active = self.store.count_active_slots(run.id)
        self.assertEqual(active, 1)

    def test_stop_flag_persists(self) -> None:
        run = self.store.create_run(
            project="Demo",
            source_stage="Backlog",
            target_stage="Review",
            concurrency=1,
            snapshot_task_ids=[1],
            task_titles={1: "One"},
        )
        self.store.request_stop(run.id)
        refreshed = self.store.get_run(run.id)
        self.assertTrue(refreshed.stop_requested)
        self.store.clear_stop(run.id)
        refreshed = self.store.get_run(run.id)
        self.assertFalse(refreshed.stop_requested)

    def test_review_cycle_limit_state_update(self) -> None:
        run = self.store.create_run(
            project="Demo",
            source_stage="Backlog",
            target_stage="Review",
            concurrency=1,
            snapshot_task_ids=[7],
            task_titles={7: "Seven"},
        )
        task = self.store.list_tasks(run.id)[0]
        updated = self.store.update_task(
            task.id,
            state=TaskState.BLOCKED,
            review_cycle=3,
            error_message="too many review cycles",
        )
        self.assertEqual(updated.state, TaskState.BLOCKED)
        self.assertEqual(updated.review_cycle, 3)


class AgentRunnerTests(unittest.TestCase):
    def test_parse_agent_json_from_fenced_block(self) -> None:
        stdout = """
Analysis complete.

```json
{"status": "blocked", "blocking_questions": ["Which API?"]}
```
"""
        parsed = parse_agent_json(stdout)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["status"], "blocked")
        self.assertEqual(parsed["blocking_questions"], ["Which API?"])

    def test_question_message_format(self) -> None:
        message = format_questions_message(["A?", "B?"])
        self.assertIn("ИИ приостановил планирование задачи", message)
        self.assertIn("1. A?", message)
        self.assertIn("2. B?", message)


if __name__ == "__main__":
    unittest.main()
