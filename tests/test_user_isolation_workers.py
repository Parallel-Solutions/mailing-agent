from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from src.generator.orchestration.orchestrator_session_state import SessionAccessDenied, get_session
from src.security.auth import Principal
from src.workers.process_manager import _count_user_active_workers


class OrchestratorSessionIsolationTests(unittest.TestCase):
    def test_get_session_rejects_foreign_owner(self) -> None:
        session_id, session = get_session(None, Principal("alice", "alice"))
        self.assertEqual(session["owner_username"], "alice")

        with self.assertRaises(SessionAccessDenied):
            get_session(session_id, Principal("bob", "bob"))


class PerUserWorkerLimitTests(unittest.TestCase):
    def test_count_user_active_workers_uses_job_owner(self) -> None:
        hold = threading.Event()

        def worker() -> None:
            hold.wait(timeout=1)

        registry = {
            "job-a": threading.Thread(target=worker, daemon=True),
            "job-b": threading.Thread(target=worker, daemon=True),
        }
        for thread in registry.values():
            thread.start()

        try:
            with patch("src.workers.process_manager.read_job_owner") as read_owner:
                read_owner.side_effect = lambda job_id: {
                    "job-a": {"owner_username": "alice"},
                    "job-b": {"owner_username": "bob"},
                }.get(job_id or "", {})
                self.assertEqual(_count_user_active_workers(registry, "alice"), 1)
                self.assertEqual(_count_user_active_workers(registry, "bob"), 1)
        finally:
            hold.set()
            for thread in registry.values():
                thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
