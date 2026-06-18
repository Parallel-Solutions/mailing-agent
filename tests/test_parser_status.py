from __future__ import annotations

import shutil
import unittest
from uuid import uuid4

from src.generator.orchestration.parser_agent import PARSER_STATE, get_parser_status
from src.jobs import resolve_job_paths, save_agent_state


class ParserStatusTests(unittest.TestCase):
    def test_get_parser_status_recovers_completed_verification_left_running(self) -> None:
        job_id = f"job-test-parser-{uuid4().hex}"
        job_paths = resolve_job_paths(job_id)
        try:
            state = {
                **PARSER_STATE,
                "municipality_name_verification": {},
                "municipality_name_verification_state": {
                    "status": "running",
                    "source": "upload",
                    "started_at": "2026-06-18T17:42:17",
                    "completed_at": None,
                    "summary_text": "Проверяю официальные названия МО: 2 из 2 строк.",
                    "processed_rows": 2,
                    "total_rows": 2,
                    "verified_rows": 2,
                    "updated_rows": 2,
                    "missing_rows": 0,
                },
            }
            save_agent_state("parser", state, job_id=job_id)

            result = get_parser_status(job_id)

            verification_state = result["municipality_name_verification_state"]
            verification_result = result["municipality_name_verification"]
            self.assertEqual(verification_state["status"], "completed")
            self.assertEqual(verification_result["status"], "ok")
            self.assertEqual(verification_result["total_rows"], 2)
            self.assertEqual(verification_result["verified_rows"], 2)
            self.assertEqual(verification_result["updated_rows"], 2)
        finally:
            shutil.rmtree(job_paths.root_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
