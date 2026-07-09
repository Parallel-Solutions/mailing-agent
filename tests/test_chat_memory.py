from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

from src.jobs import chat_memory


class ChatMemoryTests(TestCase):
    def setUp(self) -> None:
        with chat_memory._LOCK:
            chat_memory._SESSIONS.clear()

    def tearDown(self) -> None:
        with chat_memory._LOCK:
            chat_memory._SESSIONS.clear()

    def test_job_chat_session_is_restored_from_disk(self) -> None:
        Path("C:/tmp").mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir="C:/tmp") as tmp:
            root = Path(tmp)

            def fake_resolve_job_paths(job_id):
                return SimpleNamespace(root_dir=root / str(job_id or "legacy"))

            with patch("src.jobs.chat_memory.resolve_job_paths", side_effect=fake_resolve_job_paths):
                session_id, session = chat_memory.get_chat_session(
                    None,
                    namespace="documents",
                    job_id="job-test",
                )
                self.assertEqual(session["messages"], [])

                chat_memory.append_chat_turn(session_id, "hello", "saved reply")
                store_path = root / "job-test" / "state" / "chat_sessions_documents.json"
                self.assertTrue(store_path.exists())

                with chat_memory._LOCK:
                    chat_memory._SESSIONS.clear()

                restored_id, restored = chat_memory.get_chat_session(
                    session_id,
                    namespace="documents",
                    job_id="job-test",
                )

        self.assertEqual(restored_id, session_id)
        self.assertEqual(
            chat_memory.chat_history_for_prompt(restored),
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "saved reply"},
            ],
        )


if __name__ == "__main__":
    main()
