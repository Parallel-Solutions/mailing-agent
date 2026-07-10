from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.generator.generation.template_preview import (
    PREVIEW_APPROVAL_APPROVED,
    PREVIEW_APPROVAL_PENDING,
    is_template_preview_approved,
    mark_template_preview_approval,
    save_template_preview_state,
)
from src.security.auth import Principal
from src.web.documents_agent_chat import _documents_agent_preview_decision_reply
from src.web.documents_router import create_documents_router


class _FakeDocumentsAiClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.reply)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


def _workspace_temp_dir() -> tempfile.TemporaryDirectory[str]:
    root = Path("C:/tmp") / "mailing-agent-tests"
    root.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=str(root))


class TemplatePreviewGateTests(unittest.TestCase):
    def test_preview_state_must_be_explicitly_approved_for_same_mode_and_work_type(self) -> None:
        with _workspace_temp_dir() as tmp:
            root = Path(tmp)
            fake_paths = SimpleNamespace(root_dir=root)
            with patch("src.generator.generation.template_preview.resolve_job_paths", return_value=fake_paths):
                save_template_preview_state(
                    "job-preview",
                    {
                        "status": "ready",
                        "approval_status": PREVIEW_APPROVAL_PENDING,
                        "document_mode": "kp",
                        "work_type": "stp_mo",
                    },
                )

                self.assertFalse(is_template_preview_approved("job-preview", document_mode="kp", work_type="stp_mo"))

                state = mark_template_preview_approval("job-preview", approved=True, reason="ok", actor="test")

                self.assertEqual(state["approval_status"], PREVIEW_APPROVAL_APPROVED)
                self.assertTrue(is_template_preview_approved("job-preview", document_mode="kp", work_type="stp_mo"))
                self.assertFalse(is_template_preview_approved("job-preview", document_mode="contract", work_type="stp_mo"))
                self.assertFalse(is_template_preview_approved("job-preview", document_mode="kp", work_type="mngp_settlements"))

    def test_documents_chat_approval_starts_mass_generation_action(self) -> None:
        client = _FakeDocumentsAiClient('{"intent":"approve_preview","confidence":0.93,"reply":"Поняла, запускаю массовую подготовку."}')
        context = {
            "template_preview": {
                "exists": True,
                "awaiting_confirmation": True,
                "status": "ready",
                "approval_status": PREVIEW_APPROVAL_PENDING,
                "document_mode": "kp",
                "work_type": "stp_mo",
            },
            "status": {"status": "idle"},
        }

        with patch("src.web.documents_agent_chat._documents_agent_build_llm_client", return_value=client), \
             patch("src.web.documents_agent_chat.mark_template_preview_approval") as mark_approval:
            result = _documents_agent_preview_decision_reply("да, можно запускать", "job-preview", context, [])

        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "start_documents_after_preview")
        self.assertTrue(result["preview_approved"])
        mark_approval.assert_called_once()

    def test_documents_chat_unclear_answer_keeps_generation_on_hold(self) -> None:
        client = _FakeDocumentsAiClient('{"intent":"unclear","confidence":0.8,"reply":"Я не уверена, что это подтверждение."}')
        context = {
            "template_preview": {
                "exists": True,
                "awaiting_confirmation": True,
                "status": "ready",
                "approval_status": PREVIEW_APPROVAL_PENDING,
            },
            "status": {"status": "idle"},
        }

        with patch("src.web.documents_agent_chat._documents_agent_build_llm_client", return_value=client), \
             patch("src.web.documents_agent_chat.mark_template_preview_approval") as mark_approval:
            result = _documents_agent_preview_decision_reply("ну не знаю", "job-preview", context, [])

        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "hold_documents_generation")
        self.assertFalse(result["preview_approved"])
        mark_approval.assert_not_called()

    def _documents_client(self, data_path: Path, *, approved: bool):
        calls: list[dict] = []

        def check_auth() -> Principal:
            return Principal(username="admin", tenant_id="tenant-a", role="admin")

        def start_documents_thread_if_absent(job_id, **kwargs):
            calls.append({"job_id": job_id, **kwargs})
            return object(), True

        app = FastAPI()
        app.include_router(
            create_documents_router(
                check_auth=check_auth,
                prefer_existing_file=lambda primary, fallback: primary,
                compact_documents_status=lambda job_id, document_mode: {"restart_locked": False, "status": "idle"},
                get_generator_thread=lambda job_id: None,
                get_philologist_thread=lambda job_id: None,
                prime_philologist_running_state=lambda job_id, mode: {"status": "running"},
                start_documents_thread_if_absent=start_documents_thread_if_absent,
                run_documents_pipeline_background=lambda **kwargs: None,
                documents_job_key=lambda job_id: str(job_id or "__legacy__"),
                clear_philologist_stop_request=lambda job_id: None,
                get_generator_status=lambda job_id: {"status": "idle", "document_mode": "", "work_type": "", "renderer_version": ""},
                get_philologist_status=lambda job_id: {"status": "idle"},
                clear_generator_stop_request=lambda job_id: None,
                save_generator_state=lambda state, job_id: state,
                prime_generator_state=lambda **kwargs: {"status": "running"},
                request_generator_stop=lambda job_id: {"status": "stopped"},
                request_philologist_stop=lambda job_id: {"status": "stopped"},
                documents_agent_choose_reply=lambda **kwargs: {"reply": "ok"},
            )
        )
        patchers = [
            patch("src.web.documents_router.resolve_job_paths", return_value=SimpleNamespace(data_xlsx=data_path)),
            patch("src.web.documents_router.is_template_preview_approved", return_value=approved),
            patch("src.web.documents_router.append_audit_event", lambda **kwargs: None),
        ]
        return TestClient(app), calls, patchers

    def test_documents_start_rejects_without_approved_preview(self) -> None:
        with _workspace_temp_dir() as tmp:
            data_path = Path(tmp) / "data.xlsx"
            data_path.write_bytes(b"placeholder")
            client, calls, patchers = self._documents_client(data_path, approved=False)
            with patchers[0], patchers[1], patchers[2]:
                response = client.post("/api/documents/start", json={"document_mode": "kp", "work_type": "stp_mo"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(calls, [])

    def test_documents_start_allows_approved_preview(self) -> None:
        with _workspace_temp_dir() as tmp:
            data_path = Path(tmp) / "data.xlsx"
            data_path.write_bytes(b"placeholder")
            client, calls, patchers = self._documents_client(data_path, approved=True)
            with patchers[0], patchers[1], patchers[2]:
                response = client.post("/api/documents/start", json={"document_mode": "kp", "work_type": "stp_mo"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()