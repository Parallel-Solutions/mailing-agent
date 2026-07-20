from __future__ import annotations

import unittest
import uuid
from io import BytesIO
from unittest.mock import MagicMock, patch

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.campaigns.assistants.context import AssistantContext
from src.campaigns.assistants.prompts import system_prompt
from src.campaigns.assistants.service import run_editor_assistant
from src.campaigns.assistants.tools import execute_tool
from src.campaigns.assistants.tools import docx as docx_tools
from src.campaigns.assistants.tools.common import clip_snapshot
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


def _sample_docx(*paragraphs: str) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class EditorAssistantUnitTests(unittest.TestCase):
    def test_clip_snapshot_truncates_html_and_summarizes_project(self) -> None:
        clipped = clip_snapshot(
            {
                "body_html": "x" * 20000,
                "grapesjs_project": {"pages": [{}, {}], "assets": [1]},
                "chain": {
                    "version": 2,
                    "root_node_id": "root",
                    "nodes": [{"id": "root", "name": "A"}],
                    "edges": [],
                },
            }
        )
        self.assertLess(len(clipped["body_html"]), 13000)
        self.assertEqual(clipped["grapesjs_project"]["pages"], 2)
        self.assertEqual(clipped["chain"]["node_count"], 1)

    def test_chain_tools_emit_chain_set_action(self) -> None:
        ctx = AssistantContext(
            editor_kind="chain",
            resource_id="chain-1",
            owner_username="demo",
            snapshot={
                "chain": {
                    "version": 2,
                    "root_node_id": "root",
                    "nodes": [{"id": "root", "name": "Письмо 1", "kind": "email"}],
                    "edges": [],
                },
                "selected_node_id": "root",
            },
            working={
                "chain": {
                    "version": 2,
                    "root_node_id": "root",
                    "nodes": [{"id": "root", "name": "Письмо 1", "kind": "email"}],
                    "edges": [],
                },
                "selected_node_id": "root",
            },
        )
        result = execute_tool(ctx, "add_email_node", {"parent_id": "root", "name": "Письмо 2"})
        self.assertTrue(result["ok"])
        self.assertEqual(ctx.actions[-1]["type"], "chain_set")
        self.assertEqual(len(ctx.working["chain"]["nodes"]), 2)
        self.assertIn("add_email_node", ctx.tools_used)

    def test_pdf_update_fields_emits_action(self) -> None:
        ctx = AssistantContext(
            editor_kind="pdf",
            resource_id="tmpl-1",
            owner_username="demo",
            snapshot={
                "fields": [
                    {"id": "f1", "value": "old", "font_size": 12, "variable": "company"},
                ]
            },
            working={
                "fields": [
                    {"id": "f1", "value": "old", "font_size": 12, "variable": "company"},
                ]
            },
        )
        result = execute_tool(
            ctx,
            "update_fields",
            {"fields": [{"id": "f1", "value": "ООО Тест", "font_size": 11}]},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(ctx.actions[0]["type"], "update_pdf_fields")
        self.assertEqual(ctx.working["fields"][0]["value"], "ООО Тест")

    def test_visual_email_set_html_emits_action(self) -> None:
        ctx = AssistantContext(
            editor_kind="visual_email",
            resource_id="tmpl-1",
            owner_username="demo",
            snapshot={"body_html": "<p>old</p>"},
            working={"body_html": "<p>old</p>"},
        )
        result = execute_tool(ctx, "set_body_html", {"html": "<p>new {{contact_name}}</p>"})
        self.assertTrue(result["ok"])
        self.assertEqual(ctx.actions[0]["type"], "set_html")
        self.assertIn("{{contact_name}}", ctx.working["body_html"])

    def test_run_editor_assistant_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            run_editor_assistant(
                editor_kind="unknown",
                resource_id="x",
                message="hi",
                owner_username="demo",
            )

    @patch("src.campaigns.assistants.loop.build_assistant_client", return_value=None)
    def test_run_editor_assistant_without_llm(self, _client: MagicMock) -> None:
        result = run_editor_assistant(
            editor_kind="simple_email",
            resource_id="tmpl-1",
            message="Сделай тему короче",
            owner_username="demo",
            snapshot={"subject": "Длинная тема", "body_html": "<p>hi</p>"},
        )
        self.assertIn("session_id", result)
        self.assertTrue(result["reply"])
        self.assertEqual(result["actions"], [])

    def test_docx_prompt_prefers_surgical_edits(self) -> None:
        prompt = system_prompt(editor_kind="docx", snapshot={"name": "Договор"})
        self.assertIn("replace_text", prompt)
        self.assertIn("get_document_text", prompt)
        self.assertIn("rewrite_document", prompt)
        self.assertIn("не просит явно", prompt.lower())

    def test_docx_tool_schema_has_replace_not_edit_document(self) -> None:
        names = {item["function"]["name"] for item in docx_tools.TOOLS}
        self.assertIn("replace_text", names)
        self.assertIn("get_document_text", names)
        self.assertIn("rewrite_document", names)
        self.assertNotIn("edit_document", names)
        rewrite = next(item for item in docx_tools.TOOLS if item["function"]["name"] == "rewrite_document")
        self.assertIn("ТОЛЬКО", rewrite["function"]["description"])

    @patch("src.campaigns.assistants.tools.docx.template_service")
    def test_replace_text_patches_and_reloads(self, template_service: MagicMock) -> None:
        original = _sample_docx("Привет, мир!", "Второй абзац {{company}}")
        template_service.get_template.return_value = {
            "name": "Письмо",
            "template_type": "document",
            "version": {"filename": "letter.docx", "id": "v1"},
        }
        template_service.get_template_file.return_value = {
            "content": original,
            "filename": "letter.docx",
        }
        template_service.upload_file_version.return_value = {
            "name": "Письмо",
            "version": {"id": "v2", "filename": "letter.docx"},
        }
        ctx = AssistantContext(
            editor_kind="docx",
            resource_id="tmpl-1",
            owner_username="demo",
        )
        result = execute_tool(
            ctx,
            "replace_text",
            {"edits": [{"find": "мир", "replace": "коллега"}]},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(ctx.actions[0]["type"], "reload_template")
        self.assertEqual(ctx.actions[0]["reason"], "docx_patched")
        uploaded = template_service.upload_file_version.call_args.kwargs["data"]
        from src.campaigns import docx_text_edits

        text = docx_text_edits.extract_plain_text(uploaded)
        self.assertIn("коллега", text)
        self.assertIn("Второй абзац {{company}}", text)

    @patch("src.campaigns.assistants.tools.docx.template_service")
    def test_replace_text_fails_when_fragment_missing(self, template_service: MagicMock) -> None:
        template_service.get_template.return_value = {
            "name": "Письмо",
            "template_type": "document",
            "version": {"filename": "letter.docx"},
        }
        template_service.get_template_file.return_value = {
            "content": _sample_docx("Только это"),
            "filename": "letter.docx",
        }
        ctx = AssistantContext(
            editor_kind="docx",
            resource_id="tmpl-1",
            owner_username="demo",
        )
        result = execute_tool(
            ctx,
            "replace_text",
            {"edits": [{"find": "нет такого", "replace": "x"}]},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(ctx.actions, [])
        template_service.upload_file_version.assert_not_called()

    @patch("src.campaigns.assistants.tools.docx.template_ai._call_llm")
    @patch("src.campaigns.assistants.tools.docx.template_service")
    def test_rewrite_document_uploads_new_version(
        self,
        template_service: MagicMock,
        call_llm: MagicMock,
    ) -> None:
        template_service.get_template.return_value = {
            "name": "Письмо",
            "template_type": "document",
            "version": {"filename": "letter.docx"},
        }
        template_service.get_template_file.return_value = {
            "content": _sample_docx("Старый текст"),
            "filename": "letter.docx",
        }
        template_service._file_text.return_value = "Старый текст"
        call_llm.return_value = {
            "name": "Письмо",
            "title": "Письмо",
            "paragraphs": ["Новый текст целиком"],
        }
        template_service.upload_file_version.return_value = {
            "name": "Письмо",
            "version": {"id": "v3", "filename": "Письмо.docx"},
        }
        ctx = AssistantContext(
            editor_kind="docx",
            resource_id="tmpl-1",
            owner_username="demo",
            model="test-model",
        )
        result = execute_tool(
            ctx,
            "rewrite_document",
            {"instruction": "Перепиши документ заново"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(ctx.actions[0]["reason"], "docx_replaced")
        template_service.upload_file_version.assert_called_once()


class EditorAssistantApiTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"a{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        app = FastAPI()
        app.include_router(create_v1_router(check_auth=lambda: Principal(self.username, "t1", "user")))
        self.client = TestClient(app)

    @patch("src.campaigns.assistants.loop.build_assistant_client", return_value=None)
    def test_assistants_chat_endpoint(self, _client: MagicMock) -> None:
        response = self.client.post(
            "/api/v1/assistants/chat",
            json={
                "editor_kind": "visual_email",
                "resource_id": "tmpl-demo",
                "message": "Добавь приветствие",
                "snapshot": {"subject": "Тема", "body_html": "<p>hi</p>"},
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertIn("reply", payload["result"])
        self.assertIn("session_id", payload["result"])
        self.assertEqual(payload["result"]["editor_kind"], "visual_email")

    def test_assistants_chat_validates_kind(self) -> None:
        response = self.client.post(
            "/api/v1/assistants/chat",
            json={
                "editor_kind": "legacy",
                "resource_id": "x",
                "message": "hi",
            },
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
