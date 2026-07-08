from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from src.generator.knowledge.service_knowledge import find_relevant_service_docs, format_service_rag_context
from src.web.documents_agent_chat import choose_documents_agent_reply


class _FakeDocumentsAiClient:
    def __init__(self, reply: str = "ai reply") -> None:
        self.reply = reply
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.reply)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


class ServiceKnowledgeTests(unittest.TestCase):
    def test_find_relevant_service_docs_returns_gotenberg_context(self) -> None:
        docs = find_relevant_service_docs("что такое Gotenberg и зачем он нужен для PDF")

        self.assertTrue(docs)
        self.assertEqual(docs[0]["id"], "gotenberg_pdf_backend")
        self.assertIn("Docker", docs[0]["answer"])

    def test_format_service_rag_context_includes_source_ids(self) -> None:
        docs = find_relevant_service_docs("почему pdf отличается от docx")
        context = format_service_rag_context(docs)

        self.assertIn("documents_pdf_conversion", context)
        self.assertIn("Ответ:", context)

    def test_documents_chat_puts_service_rag_into_ai_context(self) -> None:
        def status_loader(_job_id):
            return {
                "status": "idle",
                "stage_text": "not started",
                "generator": {},
                "philologist": {},
            }

        client = _FakeDocumentsAiClient("rag answer from ai")
        with patch("src.web.documents_agent_chat._documents_agent_build_llm_client", return_value=client):
            result = choose_documents_agent_reply(
                "what is gotenberg for pdf?",
                job_id="job-test",
                status_loader=status_loader,
            )

        self.assertEqual(result["source"], "documents_ai")
        self.assertEqual(result["tools_used"], ["ai_context", "session_memory"])
        prompt = client.calls[0]["messages"][-1]["content"]
        self.assertIn('"service_knowledge"', prompt)
        self.assertIn("Gotenberg", prompt)
    def test_documents_chat_capabilities_go_through_ai(self) -> None:
        def status_loader(_job_id):
            return {
                "status": "completed",
                "stage_text": "done",
                "generator": {},
                "philologist": {},
            }

        client = _FakeDocumentsAiClient("capabilities from ai")
        with patch("src.web.documents_agent_chat._documents_agent_build_llm_client", return_value=client):
            result = choose_documents_agent_reply(
                "what can you do?",
                job_id="job-test",
                status_loader=status_loader,
            )

        self.assertEqual(result["reply"], "capabilities from ai")
        self.assertEqual(result["source"], "documents_ai")
        self.assertTrue(result["session_id"].startswith("documents-"))
    def test_documents_chat_text_review_context_goes_to_ai(self) -> None:
        def status_loader(_job_id):
            return {
                "status": "completed",
                "stage": "completed",
                "fixed_documents": 1,
                "documents_with_issues": 1,
                "total_documents": 2,
                "reviewed_documents": 2,
                "generator": {},
                "philologist": {
                    "fixed_documents": 1,
                    "documents_with_issues": 1,
                },
            }

        client = _FakeDocumentsAiClient("review from ai")
        with patch("src.web.documents_agent_chat._documents_agent_build_llm_client", return_value=client):
            result = choose_documents_agent_reply(
                "what was fixed in documents?",
                job_id="job-test",
                status_loader=status_loader,
            )

        self.assertEqual(result["tools_used"], ["ai_context", "session_memory"])
        prompt = client.calls[0]["messages"][-1]["content"]
        self.assertIn('"fixed_documents": 1', prompt)
        self.assertIn('"documents_with_issues": 1', prompt)
    def test_documents_chat_reports_ai_unavailable_without_legacy_fallback(self) -> None:
        def status_loader(_job_id):
            return {
                "status": "completed",
                "stage_text": "done",
                "generator": {},
                "philologist": {},
            }

        with patch("src.web.documents_agent_chat._documents_agent_build_llm_client", return_value=None):
            result = choose_documents_agent_reply(
                "why is this strange?",
                job_id="job-test",
                status_loader=status_loader,
            )

        self.assertEqual(result["source"], "documents_ai_unavailable")
        self.assertIn("AI chat is unavailable", result["reply"])
        self.assertNotIn("debug_scroll_test", result["source"])

if __name__ == "__main__":
    unittest.main()
