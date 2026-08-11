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

    def test_crossing_complaint_threshold_no_longer_auto_pauses(self) -> None:
        """The blanket account-wide auto-pause was removed as a design
        mistake — channel_guard.py's per-connection guard already isolates
        and self-heals on that connection's own error spikes. Crossing the
        threshold must still update the visible counters/rate (still used
        by /api/sender/status) but must never call pause_sending()."""
        from src.utils.config import settings
        from src.generator.delivery.send_guard import (
            evaluate_thresholds,
            get_send_guard_status,
            is_sending_paused,
            record_complaint,
            record_sent,
            resume_sending,
        )

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
            self.assertFalse(is_sending_paused())
            # Counters are process-wide sliding windows (Redis or in-memory
            # fallback), shared across the whole test run rather than reset
            # per test — assert they still moved, not exact totals.
            status = get_send_guard_status()
            self.assertGreaterEqual(status["sent"], 5)
            self.assertGreaterEqual(status["complaints"], 1)
        finally:
            settings.send_guard_min_samples = original_min
            settings.send_guard_complaint_rate_threshold = original_threshold
            resume_sending()


if __name__ == "__main__":
    unittest.main()
