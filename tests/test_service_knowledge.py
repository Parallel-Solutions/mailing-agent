from __future__ import annotations

import unittest

from src.generator.knowledge.service_knowledge import find_relevant_service_docs, format_service_rag_context
from src.web.documents_agent_chat import choose_documents_agent_reply


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

    def test_documents_chat_uses_service_rag_for_knowledge_questions(self) -> None:
        def status_loader(_job_id):
            return {
                "status": "idle",
                "stage_text": "Подготовка документов ещё не запускалась.",
                "generator": {},
                "philologist": {},
            }

        result = choose_documents_agent_reply(
            "что такое gotenberg для pdf?",
            job_id="job-test",
            status_loader=status_loader,
        )

        self.assertEqual(result["source"], "service_rag")
        self.assertIn("Gotenberg", result["reply"])
        self.assertIn("tools_used", result)


if __name__ == "__main__":
    unittest.main()
