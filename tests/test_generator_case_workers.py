from __future__ import annotations

import shutil
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.generator.generation import generator_agent


class GeneratorCaseWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp_test_generator_case_workers")
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)
        self.tmp_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def test_case_agent_rows_use_configured_thread_workers(self) -> None:
        data_path = self.tmp_dir / "data.xlsx"
        data_path.write_bytes(b"fake")
        job_paths = SimpleNamespace(
            job_id="job-test",
            root_dir=self.tmp_dir,
            data_xlsx=data_path,
            output_dir=self.tmp_dir / "output",
            batch_docx_dir=self.tmp_dir / "batch_docx",
            batch_pdf_dir=self.tmp_dir / "batch_pdf",
            templates_dir=self.tmp_dir / "templates",
            uses_legacy_layout=False,
        )
        rows = [{"ID": "1", "MUN_NAME": "A"}, {"ID": "2", "MUN_NAME": "B"}]
        started: list[int] = []
        started_lock = threading.Lock()
        both_started = threading.Event()

        def fake_process(payload, **kwargs):
            result_index, _outgoing_number, row = payload
            with started_lock:
                started.append(result_index)
                if len(started) >= 2:
                    both_started.set()
            if not both_started.wait(timeout=1.0):
                raise RuntimeError("rows were not processed concurrently")
            return {
                "result_index": result_index,
                "id": row.get("ID"),
                "status": "ok",
                "case_agent_status": "checked",
                "inflection_trace": [],
                "generated_files": {},
            }

        def fake_finalize(results, **kwargs) -> None:
            for result in results:
                final_docx = self.tmp_dir / "output" / f"{result['id']}.docx"
                final_docx.parent.mkdir(parents=True, exist_ok=True)
                final_docx.write_bytes(b"docx")
                result["files"] = {"kp_final_docx": str(final_docx)}

        with (
            patch.object(generator_agent, "resolve_job_paths", return_value=job_paths),
            patch.object(generator_agent, "_load_generator_state", return_value=dict(generator_agent.GENERATOR_STATE)),
            patch.object(generator_agent, "_save_generator_state", side_effect=lambda state, job_id=None: state),
            patch.object(generator_agent, "load_rows", return_value=(SimpleNamespace(close=lambda: None), None, rows)),
            patch.object(generator_agent, "mark_tasks_in_progress", return_value=[]),
            patch.object(generator_agent, "count_tasks_for_agent", return_value={}),
            patch.object(generator_agent, "get_tasks_for_agent", return_value=[]),
            patch.object(generator_agent, "get_recent_events", return_value=[]),
            patch.object(generator_agent, "set_task_statuses", return_value=None),
            patch.object(generator_agent, "cleanup_existing_output_dirs", return_value=None),
            patch.object(generator_agent, "process_generator_row", side_effect=fake_process),
            patch.object(generator_agent, "finalize_generated_files", side_effect=fake_finalize),
            patch.object(generator_agent, "ENABLE_CASE_AGENT", True),
            patch.object(generator_agent, "WEB_CASE_AGENT_MAX_WORKERS", 2),
        ):
            result = generator_agent.run_generator_agent(
                xlsx_path=data_path,
                job_id="job-test",
                create_pdf=False,
                auto_run_philologist=False,
                document_mode="kp",
            )

        render_timing = next(item for item in result["timings"] if item["stage"] == "render_docx")
        self.assertEqual(result["ok_rows"], 2)
        self.assertEqual(render_timing["workers"], 2)
        self.assertEqual(render_timing["web_case_agent_max_workers"], 2)
        self.assertEqual(sorted(started), [0, 1])


if __name__ == "__main__":
    unittest.main()