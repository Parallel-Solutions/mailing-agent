from __future__ import annotations

import unittest
from unittest.mock import patch
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


    def test_documents_chat_explains_capabilities(self) -> None:
        def status_loader(_job_id):
            return {
                "status": "completed",
                "stage_text": "Результат собран.",
                "generator": {},
                "philologist": {},
            }

        result = choose_documents_agent_reply(
            "что ты умеешь?",
            job_id="job-test",
            status_loader=status_loader,
        )

        self.assertIn("что сейчас с документами", result["reply"])
        self.assertIn("ничего сам не запускаю", result["reply"])
        self.assertEqual(result["tools_used"], ["get_documents_status"])

    def test_documents_chat_shows_fix_examples_without_llm(self) -> None:
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
                    "documents": [
                        {
                            "name": "Договор.docx",
                            "applied_fixes": [
                                {"issue": "неверный падеж", "suggestion": "Жигаловского муниципального округа"}
                            ],
                            "skipped_fixes": [
                                {"issue": "спорная формулировка", "reason": "нужна ручная проверка"}
                            ],
                        }
                    ]
                },
            }

        result = choose_documents_agent_reply(
            "какие исправления в документах ты сделал?",
            job_id="job-test",
            status_loader=status_loader,
        )

        self.assertEqual(result["tools_used"], ["get_text_review_summary"])
        self.assertIn("Примеры исправлений", result["reply"])
        self.assertIn("Договор.docx", result["reply"])
        self.assertIn("Жигаловского муниципального округа", result["reply"])

    def test_documents_chat_does_not_fake_generic_fallback(self) -> None:
        def status_loader(_job_id):
            return {
                "status": "completed",
                "stage_text": "Результат собран.",
                "generator": {},
                "philologist": {},
            }

        with patch("src.web.documents_agent_chat._documents_agent_build_llm_client", return_value=None):
            result = choose_documents_agent_reply(
                "почему всё такое странное?",
                job_id="job-test",
                status_loader=status_loader,
            )

        self.assertEqual(result["source"], "documents_ai_unavailable")
        self.assertIn("не смог получить ответ от AI", result["reply"])
        self.assertNotIn("Я на связи", result["reply"])

if __name__ == "__main__":
    unittest.main()
