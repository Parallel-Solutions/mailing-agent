from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet

from src.campaigns.connection_service import create_connection, update_connection
from src.campaigns.service import (
    _effective_rate_limits,
    _min_positive,
    create_campaign,
    upsert_schedule,
)
from src.infra.db import session_scope
from src.infra.models import CampaignRecipient
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class RateLimitHelpersTests(unittest.TestCase):
    def test_min_positive_unlimited_when_all_zero(self) -> None:
        self.assertEqual(_min_positive(0, 0), 0)

    def test_min_positive_picks_only_positive(self) -> None:
        self.assertEqual(_min_positive(0, 40), 40)
        self.assertEqual(_min_positive(10, 0), 10)

    def test_min_positive_takes_minimum(self) -> None:
        self.assertEqual(_min_positive(100, 25), 25)

    def test_effective_rate_limits_combines_schedule_and_connection(self) -> None:
        hour, day = _effective_rate_limits(
            schedule_max_per_hour=100,
            schedule_max_per_day=0,
            connection_max_per_hour=40,
            connection_max_per_day=200,
        )
        self.assertEqual(hour, 40)
        self.assertEqual(day, 200)


class ConnectionRateLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.owner = f"owner-{uuid.uuid4().hex[:8]}"
        create_user(self.owner, "Pass12345!")

    def test_create_and_update_api_connection_rate_limits(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "rusender",
                "email": "verified@example.com",
                "api_token": "rs_ck_secret",
                "max_per_hour": 30,
                "max_per_day": 200,
            },
        )
        self.assertEqual(created["max_per_hour"], 30)
        self.assertEqual(created["max_per_day"], 200)

        updated = update_connection(
            created["id"],
            self.owner,
            {"max_per_hour": 10, "max_per_day": 50},
        )
        self.assertEqual(updated["max_per_hour"], 10)
        self.assertEqual(updated["max_per_day"], 50)

    def test_negative_rate_limits_clamped_to_zero(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "mailopost",
                "email": "verified@example.com",
                "api_token": "mp_token",
                "max_per_hour": -5,
                "max_per_day": -1,
            },
        )
        self.assertEqual(created["max_per_hour"], 0)
        self.assertEqual(created["max_per_day"], 0)

    def test_upsert_schedule_preview_respects_connection_limits(self) -> None:
        connection = create_connection(
            self.owner,
            {
                "transport": "rusender",
                "email": "verified@example.com",
                "api_token": "rs_ck_secret",
                "max_per_hour": 5,
                "max_per_day": 0,
            },
        )
        campaign = create_campaign(
            self.owner,
            {
                "name": "Rate limit campaign",
                "smtp_mailbox_id": connection["id"],
                "transport": "rusender",
            },
        )
        with session_scope() as session:
            for index in range(20):
                session.add(
                    CampaignRecipient(
                        campaign_id=campaign["id"],
                        row_index=index,
                        company=f"Company {index}",
                        contact_name="Contact",
                        email=f"user{index}@example.com",
                        email_fallback="",
                        region="",
                        source="test",
                        validation_status="valid",
                        excluded=False,
                        send_status="pending",
                    )
                )
            session.flush()

        schedule = upsert_schedule(
            campaign["id"],
            self.owner,
            {
                "batch_size": 25,
                "interval_seconds": 60,
                "max_per_hour": 100,
                "max_per_day": 0,
                "send_immediately": True,
                "weekdays": list(range(7)),
                "time_windows": [{"start": "00:00", "end": "23:59"}],
            },
        )
        preview = schedule["preview"] or {}
        batches = preview.get("batches") or []
        self.assertTrue(batches)
        self.assertTrue(all(int(batch["size"]) <= 5 for batch in batches))
        self.assertEqual(sum(int(batch["size"]) for batch in batches), 20)


if __name__ == "__main__":
    unittest.main()
