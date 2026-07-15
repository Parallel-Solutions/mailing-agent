from __future__ import annotations

import unittest

from tests.bootstrap import reset_test_database


class SuppressionEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_test_database()

    def test_upsert_and_check_suppression(self) -> None:
        from src.generator.delivery.suppression_store import is_suppressed, upsert_suppression

        upsert_suppression("User@Example.com", reason="hard_bounce", source="test")
        suppressed, reason = is_suppressed("user@example.com")
        self.assertTrue(suppressed)
        self.assertEqual(reason, "hard_bounce")

    def test_filter_suppressed_recipients(self) -> None:
        from src.generator.delivery.sender_agent import _filter_suppressed_recipients
        from src.generator.delivery.suppression_store import upsert_suppression

        upsert_suppression("blocked@example.com", reason="unsubscribe", source="test")
        attempts: list[dict] = []
        allowed = _filter_suppressed_recipients(
            ["ok@example.com", "blocked@example.com"],
            preflight_attempts=attempts,
        )
        self.assertEqual(allowed, ["ok@example.com"])
        self.assertEqual(len(attempts), 1)
        self.assertIn("стоп-листе", attempts[0]["error"])


if __name__ == "__main__":
    unittest.main()
