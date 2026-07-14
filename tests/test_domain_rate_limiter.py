from __future__ import annotations

import unittest

from src.generator.delivery.domain_rate_limiter import acquire_domain_slot, recipient_domain_bucket


class DomainRateLimiterTests(unittest.TestCase):
    def test_recipient_domain_bucket_mapping(self) -> None:
        self.assertEqual(recipient_domain_bucket("user@gmail.com"), "gmail.com")
        self.assertEqual(recipient_domain_bucket("user@mail.ru"), "mail.ru")
        self.assertEqual(recipient_domain_bucket("user@unknown.example"), "other")

    def test_acquire_domain_slot_allows_under_limit(self) -> None:
        allowed, wait_seconds, bucket = acquire_domain_slot("test-limit@example.com")
        self.assertTrue(allowed)
        self.assertEqual(wait_seconds, 0.0)
        self.assertEqual(bucket, "other")


if __name__ == "__main__":
    unittest.main()
