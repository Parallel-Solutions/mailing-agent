from __future__ import annotations

import unittest
from unittest.mock import patch

from src.generator.delivery.manager_actions import append_manager_action, load_manager_actions
from src.generator.delivery.manager_stats import build_recipient_detail, make_row_key
from tests.bootstrap import bootstrap_test_runtime, reset_test_database


class ManagerActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)

    def test_append_and_load_manager_action(self) -> None:
        record = append_manager_action(
            "job-actions",
            row_id="42",
            recipient_email="manager@example.com",
            organization="ООО Тест",
            recipient_name="Иван",
            action_type="call",
            responsible_manager="alex",
            due_at="2026-06-03T11:00:00",
            comment="Перезвонить завтра",
            priority=True,
            created_by="alex",
        )
        self.assertEqual(record["action_type"], "call")
        actions = load_manager_actions("job-actions")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["recipient_email"], "manager@example.com")
        self.assertTrue(actions[0]["priority"])

    def test_recipient_detail_includes_action_history(self) -> None:
        job_id = "job-actions"
        row_id = "7"
        email = "contact@example.com"
        row_key = make_row_key(job_id, row_id, email)
        delivery_row = {
            "row_id": row_id,
            "mun_name": "ООО Тест",
            "recipient": email,
            "provider": "rusender",
            "provider_status": "opened",
            "recipient_role": "primary",
            "sent_at": "2026-05-01T10:00:00",
            "sent_at_timestamp": "2026-05-01T10:00:00",
            "checked_at": "2026-05-01T11:00:00",
        }
        append_manager_action(
            job_id,
            row_id=row_id,
            recipient_email=email,
            organization="ООО Тест",
            recipient_name=email,
            action_type="call",
            created_by="alex",
        )
        with patch("src.generator.delivery.manager_stats._load_delivery_for_jobs", return_value=[{
            **delivery_row,
            "job_id": job_id,
            "row_key": row_key,
            "organization": "ООО Тест",
            "recipient_name": email,
            "email": email,
            "role": "primary",
            "role_label": "Основной",
            "manager_status": {"key": "opened", "label": "Открыто", "tone": "good", "category": "success"},
            "interest": {"key": "high", "label": "Высокий", "tone": "good"},
            "recommended_action": {"key": "call", "label": "Перезвонить"},
            "next_action": {"key": "call", "label": "Перезвонить"},
            "last_event_at": "2026-05-01T11:00:00",
            "last_event_label": "Открыто",
            "attempts": 1,
            "bounce_reason": "other",
            "bounce_reason_label": "Прочее",
            "email_domain_provider": "Другие",
        }]):
            detail = build_recipient_detail(row_key)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["next_action"]["key"], "call")
        self.assertEqual(len(detail["action_history"]), 1)


if __name__ == "__main__":
    unittest.main()
