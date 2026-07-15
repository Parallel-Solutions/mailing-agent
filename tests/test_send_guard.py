from __future__ import annotations

import unittest

from tests.bootstrap import reset_test_database


class SendGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def test_pause_and_resume(self) -> None:
        from src.generator.delivery.send_guard import (
            get_send_guard_status,
            is_sending_paused,
            pause_sending,
            resume_sending,
        )

        self.assertFalse(is_sending_paused())
        pause_sending("test pause")
        self.assertTrue(is_sending_paused())
        status = get_send_guard_status()
        self.assertTrue(status["paused"])
        self.assertIn("test pause", status["pause_reason"])
        resume_sending()
        self.assertFalse(is_sending_paused())

    def test_complaint_threshold_triggers_pause(self) -> None:
        from src.utils.config import settings
        from src.generator.delivery.send_guard import evaluate_thresholds, is_sending_paused, record_complaint, record_sent, resume_sending

        resume_sending()
        original_min = settings.send_guard_min_samples
        original_threshold = settings.send_guard_complaint_rate_threshold
        try:
            settings.send_guard_min_samples = 5
            settings.send_guard_complaint_rate_threshold = 0.1
            for _ in range(5):
                record_sent()
            record_complaint()
            evaluate_thresholds()
            self.assertTrue(is_sending_paused())
        finally:
            settings.send_guard_min_samples = original_min
            settings.send_guard_complaint_rate_threshold = original_threshold
            resume_sending()


if __name__ == "__main__":
    unittest.main()
