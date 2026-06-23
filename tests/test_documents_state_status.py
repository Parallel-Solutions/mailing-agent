from __future__ import annotations

import unittest
from pathlib import Path

from src.jobs.state import _compact_state_for_primary, _should_split_state
from src.web import documents_service


class DocumentsStateStatusTests(unittest.TestCase):
    def _configure_documents_service(
        self,
        *,
        generator_state: dict,
        philologist_state: dict,
        documents_thread: object | None = None,
    ) -> None:
        documents_service._deps.clear()
        documents_service.configure_documents_service(
            compact_generator_status=lambda state: state,
            get_generator_status=lambda job_id: generator_state,
            compact_philologist_status=lambda state: state,
            get_philologist_status=lambda job_id, include_details=False: philologist_state,
            get_documents_thread=lambda job_id: documents_thread,
            get_generator_thread=lambda job_id: object() if str(generator_state.get("status") or "") == "running" else None,
            get_philologist_thread=lambda job_id: object() if str(philologist_state.get("status") or "") in {"running", "finalizing"} else None,
            save_generator_state=lambda state, job_id: state,
            save_philologist_state=lambda state, job_id: state,
            build_job_readiness_result=lambda job_id, **kwargs: {"generator_ready": True, "generator_reason": ""},
        )

    def test_philologist_running_state_is_split_from_primary_file(self) -> None:
        state = {
            "status": "running",
            "processed_documents": 1,
            "total_documents": 2,
            "documents": [{"path": "one.docx", "issue_count": 1}],
            "tool_trace": [{"action": "review_docx"}],
        }

        primary = _compact_state_for_primary("philologist", state, Path("philologist.details.json"))

        self.assertTrue(_should_split_state("philologist", state))
        self.assertEqual(primary["status"], "running")
        self.assertEqual(primary["documents"], [])
        self.assertEqual(primary["documents_count"], 1)
        self.assertEqual(primary["tool_trace"], [])
        self.assertEqual(primary["tool_trace_count"], 1)

    def test_completed_philologist_count_makes_documents_completed(self) -> None:
        saved_philologist_states: list[dict] = []
        generator_state = {
            "status": "completed",
            "stage": "completed",
            "total_rows": 611,
            "processed_rows": 611,
            "output_file_count": 2444,
        }
        philologist_state = {
            "status": "finalizing",
            "total_documents": 1222,
            "processed_documents": 1222,
            "fixed_documents": 1222,
            "summary_text": "Документы проверены.",
        }

        documents_service._deps.clear()
        documents_service.configure_documents_service(
            compact_generator_status=lambda state: state,
            get_generator_status=lambda job_id: generator_state,
            compact_philologist_status=lambda state: state,
            get_philologist_status=lambda job_id, include_details=False: philologist_state,
            get_documents_thread=lambda job_id: None,
            get_generator_thread=lambda job_id: None,
            get_philologist_thread=lambda job_id: None,
            save_generator_state=lambda state, job_id: state,
            save_philologist_state=lambda state, job_id: saved_philologist_states.append(dict(state)),
            build_job_readiness_result=lambda job_id, **kwargs: {"generator_ready": True, "generator_reason": ""},
        )

        result = documents_service.compact_documents_status("job-test")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stage"], "completed")
        self.assertEqual(result["progress_percent"], 100)
        self.assertEqual(saved_philologist_states[-1]["status"], "completed")

    def test_finalize_progress_uses_real_work_units_without_public_pdf_step(self) -> None:
        generator_state = {
            "status": "running",
            "stage": "convert_pdf",
            "total_rows": 100,
            "processed_rows": 100,
            "staged_docx_count": 200,
            "pdf_total": 200,
            "pdf_processed": 100,
            "output_file_count": 0,
        }
        philologist_state = {
            "status": "idle",
            "total_documents": 0,
            "processed_documents": 0,
        }

        documents_service._deps.clear()
        documents_service.configure_documents_service(
            compact_generator_status=lambda state: state,
            get_generator_status=lambda job_id: generator_state,
            compact_philologist_status=lambda state: state,
            get_philologist_status=lambda job_id, include_details=False: philologist_state,
            get_documents_thread=lambda job_id: object(),
            get_generator_thread=lambda job_id: object(),
            get_philologist_thread=lambda job_id: None,
            save_generator_state=lambda state, job_id: state,
            save_philologist_state=lambda state, job_id: state,
            build_job_readiness_result=lambda job_id, **kwargs: {"generator_ready": True, "generator_reason": ""},
        )

        result = documents_service.compact_documents_status("job-test")

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["stage"], "ready")
        self.assertEqual([part["id"] for part in result["progress_units"]["parts"]], ["generate", "review", "finalize"])
        self.assertEqual(result["progress_units"]["parts"][2]["done"], 100)
        self.assertEqual(result["progress_units"]["parts"][2]["total"], 200)
        self.assertGreater(result["progress_percent"], 40)
        self.assertLess(result["progress_percent"], 60)
        chat_events = result["ui"]["chat_events"]
        self.assertTrue(any(event["id"] == "documents:stage:finalize_output" for event in chat_events))

    def test_completed_worker_recovers_stale_finalize_generator_state(self) -> None:
        saved_generator_states: list[dict] = []
        generator_state = {
            "status": "running",
            "stage": "finalize_output",
            "total_rows": 2,
            "processed_rows": 2,
            "staged_docx_count": 2,
            "pdf_total": 2,
            "pdf_processed": 0,
            "staged_pdf_count": 0,
            "output_file_count": 4,
            "document_mode": "kp",
        }
        philologist_state = {
            "status": "completed",
            "total_documents": 2,
            "processed_documents": 2,
        }

        documents_service._deps.clear()
        documents_service.configure_documents_service(
            compact_generator_status=lambda state: state,
            get_generator_status=lambda job_id: generator_state,
            compact_philologist_status=lambda state: state,
            get_philologist_status=lambda job_id, include_details=False: philologist_state,
            get_documents_thread=lambda job_id: None,
            get_generator_thread=lambda job_id: None,
            get_philologist_thread=lambda job_id: None,
            save_generator_state=lambda state, job_id: saved_generator_states.append(dict(state)),
            save_philologist_state=lambda state, job_id: state,
            build_job_readiness_result=lambda job_id, **kwargs: {
                "generator_ready": True,
                "generator_reason": "",
                "output_docx_count": 2,
                "output_pdf_count": 2,
            },
        )

        result = documents_service.compact_documents_status("job-test", document_mode="kp")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stage"], "completed")
        self.assertEqual(result["progress_percent"], 100)
        self.assertEqual(saved_generator_states[-1]["status"], "completed")
        self.assertEqual(saved_generator_states[-1]["pdf_processed"], 2)
        self.assertEqual(saved_generator_states[-1]["staged_pdf_count"], 2)
    def test_running_documents_progress_never_reaches_100(self) -> None:
        generator_state = {
            "status": "running",
            "stage": "finalize_output",
            "total_rows": 100,
            "processed_rows": 100,
            "staged_docx_count": 200,
            "pdf_total": 200,
            "pdf_processed": 200,
            "staged_pdf_count": 200,
            "output_file_count": 200,
        }
        philologist_state = {
            "status": "completed",
            "total_documents": 200,
            "processed_documents": 200,
        }

        documents_service._deps.clear()
        documents_service.configure_documents_service(
            compact_generator_status=lambda state: state,
            get_generator_status=lambda job_id: generator_state,
            compact_philologist_status=lambda state: state,
            get_philologist_status=lambda job_id, include_details=False: philologist_state,
            get_documents_thread=lambda job_id: object(),
            get_generator_thread=lambda job_id: object(),
            get_philologist_thread=lambda job_id: None,
            save_generator_state=lambda state, job_id: state,
            save_philologist_state=lambda state, job_id: state,
            build_job_readiness_result=lambda job_id, **kwargs: {"generator_ready": True, "generator_reason": ""},
        )

        result = documents_service.compact_documents_status("job-test")

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["progress_percent"], 99)

    def test_documents_chat_greeting_does_not_dump_status_stats(self) -> None:
        documents_service._deps.clear()

        payload = documents_service.documents_agent_choose_reply("привет", job_id="job-test")

        self.assertIn("reply", payload)
        self.assertIn("Привет", payload["reply"])
        self.assertEqual(payload["tools_used"], [])
        self.assertNotIn("Готово 100 из 100", payload["reply"])
        self.assertNotIn("Последние события", payload["reply"])

    def test_documents_chat_status_question_still_reports_status(self) -> None:
        self._configure_documents_service(
            documents_thread=object(),
            generator_state={
                "status": "running",
                "stage": "convert_pdf",
                "total_rows": 100,
                "processed_rows": 100,
                "staged_docx_count": 200,
                "pdf_total": 200,
                "pdf_processed": 60,
            },
            philologist_state={
                "status": "idle",
                "total_documents": 0,
                "processed_documents": 0,
            },
        )

        payload = documents_service.documents_agent_choose_reply("что сейчас происходит?", job_id="job-test")

        self.assertIn("Сейчас идёт", payload["reply"])
        self.assertIn("Собираю результат: 60 из 200", payload["reply"])
        self.assertEqual(payload["tools_used"], ["get_current_step"])

    def test_documents_pipeline_generates_reviews_then_finalizes_output(self) -> None:
        calls: list[tuple] = []
        generator_state = {"status": "idle"}
        philologist_state = {"status": "idle"}

        def get_generator_status(job_id: str | None) -> dict:
            return generator_state

        def run_generator_agent(**kwargs) -> dict:
            calls.append(("generator", kwargs.get("create_pdf"), kwargs.get("auto_run_philologist")))
            generator_state.update({"status": "completed", "stage": "completed"})
            return dict(generator_state)

        def get_philologist_status(job_id: str | None, include_details: bool = False) -> dict:
            return philologist_state

        def run_philologist(**kwargs) -> dict:
            calls.append(("philologist", kwargs.get("mode")))
            philologist_state.update({"status": "completed"})
            return dict(philologist_state)

        def finalize_documents_output(**kwargs) -> dict:
            calls.append(("finalize", kwargs.get("job_id")))
            generator_state.update({"status": "completed", "stage": "completed"})
            return dict(generator_state)

        documents_service._deps.clear()
        documents_service.configure_documents_service(
            get_generator_status=get_generator_status,
            clear_generator_stop_request=lambda job_id: calls.append(("clear_generator", job_id)),
            run_generator_agent=run_generator_agent,
            get_philologist_status=get_philologist_status,
            clear_philologist_stop_request=lambda job_id: calls.append(("clear_philologist", job_id)),
            run_philologist=run_philologist,
            save_philologist_state=lambda state, job_id: calls.append(("save_philologist", job_id)),
            save_generator_state=lambda state, job_id: calls.append(("save_generator", job_id)),
            finalize_documents_output=finalize_documents_output,
            schedule_output_archive_build=lambda job_id: calls.append(("archive", job_id)),
            load_generator_state=lambda job_id: {"stop_requested": False},
            load_philologist_state=lambda job_id: {"stop_requested": False},
            unregister_documents_thread=lambda job_id: calls.append(("unregister", job_id)),
            logger=type("Logger", (), {"exception": lambda *args, **kwargs: None})(),
        )

        documents_service.run_documents_pipeline_background(
            xlsx_path=Path("data.xlsx"),
            job_id="job-test",
            mode="fast",
        )

        self.assertEqual(
            calls,
            [
                ("clear_generator", "job-test"),
                ("generator", False, False),
                ("clear_philologist", "job-test"),
                ("philologist", "fast"),
                ("save_philologist", "job-test"),
                ("finalize", "job-test"),
                ("archive", "job-test"),
                ("unregister", "job-test"),
            ],
        )

    def test_documents_chat_error_question_uses_error_tool(self) -> None:
        self._configure_documents_service(
            generator_state={
                "status": "error",
                "stage": "render_docx",
                "summary_text": "Не удалось подготовить документы.",
                "error_rows": 2,
            },
            philologist_state={
                "status": "idle",
                "total_documents": 0,
                "processed_documents": 0,
            },
        )

        payload = documents_service.documents_agent_choose_reply("есть ошибки?", job_id="job-test")

        self.assertEqual(payload["tools_used"], ["get_errors"])
        self.assertIn("ошиб", payload["reply"].lower())

    def test_documents_chat_scroll_test_returns_long_reply(self) -> None:
        documents_service._deps.clear()

        payload = documents_service.documents_agent_choose_reply("/test-scroll", job_id="job-test")

        self.assertEqual(payload["source"], "debug_scroll_test")
        self.assertEqual(payload["tools_used"], [])
        self.assertIn("Строка 40", payload["reply"])
        self.assertGreater(len(payload["reply"]), 900)


if __name__ == "__main__":
    unittest.main()
