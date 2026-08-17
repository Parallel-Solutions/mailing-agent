from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from src.campaigns.chain_consent_service import (
    ACTION_MATERIALS_REQUEST,
    ACTION_SUBSCRIBE,
    ACTION_UNSUBSCRIBE,
    MARKETING_CONSENT_TTL_DAYS,
    get_consent_stats,
    has_active_marketing_consent,
    record_materials_request,
    record_subscribe,
    record_unsubscribe,
)
from src.generator.delivery.suppression_store import is_suppressed
from tests.bootstrap import bootstrap_test_runtime


class ChainConsentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.security.user_store import create_user
        from src.campaigns.service import create_campaign

        self.username = f"consent{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Consent test"})
        self.campaign_id = self.campaign["id"]
        self.recipient_id = 42
        self.email = "consent@example.com"
        self.token = str(uuid.uuid4())

    def test_record_subscribe_sets_one_year_expiry(self) -> None:
        before = datetime.now(timezone.utc)
        result = record_subscribe(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-sub",
            edge_id="edge-sub",
            token=self.token,
        )
        self.assertTrue(result["created"])
        self.assertEqual(result["action"], ACTION_SUBSCRIBE)
        expires_at = datetime.fromisoformat(result["expires_at"])
        delta = expires_at - before
        self.assertGreaterEqual(delta.days, MARKETING_CONSENT_TTL_DAYS - 1)

    def test_record_subscribe_idempotent(self) -> None:
        first = record_subscribe(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-sub",
            edge_id="edge-sub",
            token=self.token,
        )
        second = record_subscribe(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-sub",
            edge_id="edge-sub",
            token=self.token,
        )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])

    def test_record_unsubscribe_adds_suppression(self) -> None:
        token = str(uuid.uuid4())
        result = record_unsubscribe(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-unsub",
            edge_id="edge-unsub",
            token=token,
        )
        self.assertTrue(result["created"])
        self.assertEqual(result["action"], ACTION_UNSUBSCRIBE)
        suppressed, reason = is_suppressed(self.email)
        self.assertTrue(suppressed)
        self.assertEqual(reason, "unsubscribe")

    def test_record_materials_request_is_idempotent(self) -> None:
        first = record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-materials",
            edge_id="edge-materials",
            token=self.token,
        )
        second = record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-materials",
            edge_id="edge-materials",
            token=self.token,
        )
        self.assertEqual(first["action"], ACTION_MATERIALS_REQUEST)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])

    def test_get_consent_stats(self) -> None:
        record_subscribe(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-sub",
            edge_id="edge-sub",
            token=str(uuid.uuid4()),
        )
        record_unsubscribe(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id + 1,
            email="other@example.com",
            node_id="node-unsub",
            edge_id="edge-unsub",
            token=str(uuid.uuid4()),
        )
        record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id + 2,
            email="materials@example.com",
            node_id="node-materials",
            edge_id="edge-materials",
            token=str(uuid.uuid4()),
        )
        stats = get_consent_stats(self.campaign_id)
        self.assertEqual(stats["subscribe"]["count"], 1)
        self.assertEqual(stats["unsubscribe"]["count"], 1)
        self.assertEqual(stats["materials_request"]["count"], 1)

    def test_has_active_marketing_consent(self) -> None:
        record_subscribe(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-sub",
            edge_id="edge-sub",
            token=str(uuid.uuid4()),
        )
        self.assertTrue(has_active_marketing_consent(self.email))
        self.assertFalse(has_active_marketing_consent(self.email, at=datetime.now(timezone.utc) + timedelta(days=400)))


if __name__ == "__main__":
    unittest.main()
