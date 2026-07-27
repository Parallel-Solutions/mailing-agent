from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from src.workers import queue_worker


class QueueWorkerHealthTests(unittest.TestCase):
    def test_long_running_child_refreshes_container_heartbeat(self) -> None:
        process = MagicMock()
        process.pid = 4321
        process.returncode = 0
        process.poll.side_effect = [None, None, 0]
        process.wait.side_effect = subprocess.TimeoutExpired("worker", timeout=30)
        task = {
            "id": "task-1",
            "task_type": "documents",
            "job_id": None,
            "attempt": 1,
        }

        with (
            patch.object(queue_worker, "_sync_workspace"),
            patch.object(queue_worker.subprocess, "Popen", return_value=process),
            patch.object(queue_worker, "touch_heartbeat") as touch_heartbeat,
            patch.object(queue_worker, "heartbeat_task", return_value=True),
            patch.object(queue_worker, "is_cancel_requested", return_value=False),
            patch.object(queue_worker, "complete_task") as complete_task,
        ):
            queue_worker._run_claimed_task(task, "worker-1")

        touch_heartbeat.assert_called_once_with()
        complete_task.assert_called_once_with(
            task_id="task-1",
            worker_id="worker-1",
            result={"return_code": 0, "pid": 4321},
        )


if __name__ == "__main__":
    unittest.main()
