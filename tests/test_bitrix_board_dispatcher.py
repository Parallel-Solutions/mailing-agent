from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.bitrix_board.agent_runner import format_questions_message
from src.bitrix_board.bitrix_client import TaskSummary
from src.bitrix_board.config import BoardConfig, WebhookConfig
from src.bitrix_board.dispatcher import BoardDispatcher
from src.bitrix_board.states import TaskState
from src.bitrix_board.store import BoardStore


def _config_for(tmp: Path) -> BoardConfig:
    return BoardConfig(
        webhook=WebhookConfig(
            base_url="https://example.test/rest/1/token",
            user_id=1,
            origin="https://example.test",
            token="token",
        ),
        poll_interval_seconds=60,
        db_path=tmp / "state.db",
        worktrees_dir=tmp / "worktrees",
        repo_root=tmp / "repo",
        max_review_cycles=3,
        agent_bin="agent",
        default_group_id=None,
        repo_group_map={},
        dispatcher_pid_path=tmp / "dispatcher.pid",
        plan_stage_name="план сформирован",
        agent_backend="sdk",
        agent_runtime="local",
        cursor_api_key=None,
        agent_model="composer-2.5",
        cloud_repo_url=None,
    )


class DispatcherLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = _config_for(self.root)
        self.store = BoardStore(self.config.db_path)
        self.bitrix = MagicMock()
        self.bitrix.webhook_user_id = 1
        self.dispatcher = BoardDispatcher(self.config, self.store, self.bitrix)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_start_run_uses_bitrix_snapshot(self) -> None:
        self.bitrix.list_tasks = MagicMock()
        with patch(
            "src.bitrix_board.dispatcher.list_tasks_in_stages",
            return_value=[
                TaskSummary(
                    id=10,
                    title="Task 10",
                    description=None,
                    status=2,
                    stage_id=1,
                    stage_name="Backlog",
                    group_id=5,
                    raw={},
                )
            ],
        ):
            run = self.dispatcher.start_run(
                project="Demo",
                source_stage="Backlog",
                target_stage="Review",
                concurrency=3,
            )
        self.assertEqual(run.snapshot_task_ids, [10])

    def test_poll_moves_task_to_ready_to_resume(self) -> None:
        run = self.store.create_run(
            project="Demo",
            source_stage="Backlog",
            target_stage="Review",
            concurrency=1,
            snapshot_task_ids=[42],
            task_titles={42: "Task"},
        )
        task = self.store.list_tasks(run.id)[0]
        self.store.update_task(
            task.id,
            state=TaskState.WAITING_FOR_ANSWER,
            questions_json={"questions": ["Which option?"]},
            last_chat_message_id="100",
        )

        message = MagicMock()
        message.id = 101
        message.author_id = 2
        message.author_name = "User"
        message.date = "2026-01-01"
        message.text = "Use option A"
        message.plain_text = "Use option A"

        with patch(
            "src.bitrix_board.dispatcher.list_task_messages",
            return_value=[message],
        ):
            self.dispatcher._check_task_answers(self.store.get_task_by_bitrix_id(run.id, 42))

        refreshed = self.store.get_task_by_bitrix_id(run.id, 42)
        assert refreshed is not None
        self.assertEqual(refreshed.state, TaskState.READY_TO_RESUME)
        self.assertEqual(len(refreshed.clarifications_json or []), 1)

    def test_question_dedup_skips_repeat_hash(self) -> None:
        run = self.store.create_run(
            project="Demo",
            source_stage="Backlog",
            target_stage="Review",
            concurrency=1,
            snapshot_task_ids=[7],
            task_titles={7: "Task"},
        )
        task = self.store.list_tasks(run.id)[0]
        message = format_questions_message(["Question?"])
        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        self.store.update_task(
            task.id,
            state=TaskState.WAITING_FOR_ANSWER,
            last_question_hash=message_hash,
        )

        with patch("src.bitrix_board.dispatcher.add_task_message") as add_message:
            self.dispatcher._send_questions(
                self.store.get_task_by_bitrix_id(run.id, 7),
                ["Question?"],
            )
            add_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
