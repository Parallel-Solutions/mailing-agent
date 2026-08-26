from __future__ import annotations

import hashlib
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select

from src.campaigns.chain_consent_service import (
    ACTION_MATERIALS_REQUEST,
    ACTION_SUBSCRIBE,
    ACTION_UNSUBSCRIBE,
    MARKETING_CONSENT_TTL_DAYS,
    MaterialsConsentDocumentError,
    ensure_materials_request_document,
    get_consent_stats,
    has_active_marketing_consent,
    record_materials_request,
    record_subscribe,
    record_unsubscribe,
)
from src.generator.delivery.suppression_store import is_suppressed
from src.infra.db import session_scope
from src.infra.models import CampaignChainConsentEvent
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
        evidence = {"job_id": self.campaign["job_id"], "material_names": ["КП МНГП"]}
        first = record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-materials",
            edge_id="edge-materials",
            token=self.token,
            ip="203.0.113.42",
            user_agent="Test Browser",
            evidence=evidence,
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
        with session_scope() as session:
            event = session.scalar(
                select(CampaignChainConsentEvent).where(
                    CampaignChainConsentEvent.token == self.token
                )
            )
            assert event is not None
            self.assertEqual(event.confirmed_ip, "203.0.113.42")
            self.assertEqual(event.confirmed_user_agent, "Test Browser")
            self.assertEqual(event.evidence_payload["material_names"], ["КП МНГП"])
            self.assertEqual(event.document_status, "pending")

    @patch("src.campaigns.chain_consent_service.get_bytes", return_value=b"stored-consent")
    @patch("src.campaigns.chain_consent_service.put_upload")
    @patch(
        "src.campaigns.chain_consent_service.write_consent_document",
        return_value=hashlib.sha256(b"stored-consent").hexdigest(),
    )
    def test_ensure_materials_request_document_marks_event_ready(
        self,
        mock_write,
        mock_upload,
        mock_get_bytes,
    ) -> None:
        record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-materials",
            edge_id="edge-materials",
            token=self.token,
            ip="203.0.113.42",
            user_agent="Test Browser",
            evidence={
                "job_id": self.campaign["job_id"],
                "material_names": ["КП МНГП"],
                "consent_text_version": "materials-consent-v1",
            },
        )

        result = ensure_materials_request_document(self.token)

        self.assertEqual(result["document_status"], "ready")
        self.assertTrue(result["consent_document_path"].startswith("consents/"))
        mock_write.assert_called_once()
        mock_upload.assert_called_once()
        mock_get_bytes.assert_called_once()
        with session_scope() as session:
            event = session.scalar(
                select(CampaignChainConsentEvent).where(
                    CampaignChainConsentEvent.token == self.token
                )
            )
            assert event is not None
            self.assertEqual(event.document_status, "ready")
            self.assertEqual(
                event.consent_document_sha256,
                hashlib.sha256(b"stored-consent").hexdigest(),
            )
            self.assertEqual(event.consent_document_path, result["consent_document_path"])

    @patch("src.campaigns.chain_consent_service.get_bytes", return_value=b"stored-consent")
    @patch("src.campaigns.chain_consent_service.put_upload")
    @patch("src.campaigns.chain_consent_service.write_consent_document")
    def test_concurrent_document_generation_uses_single_writer(
        self,
        mock_write,
        mock_upload,
        mock_get_bytes,
    ) -> None:
        digest = hashlib.sha256(b"stored-consent").hexdigest()

        def slow_write(*_args, **_kwargs):
            time.sleep(0.2)
            return digest

        mock_write.side_effect = slow_write
        record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-materials",
            edge_id="edge-materials",
            token=self.token,
            evidence={"job_id": self.campaign["job_id"]},
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: ensure_materials_request_document(self.token),
                    range(2),
                )
            )

        self.assertEqual({item["document_status"] for item in results}, {"ready"})
        self.assertEqual({item["consent_document_sha256"] for item in results}, {digest})
        mock_write.assert_called_once()
        mock_upload.assert_called_once()
        mock_get_bytes.assert_called_once()

    @patch("src.campaigns.chain_consent_service.get_bytes", return_value=b"different")
    @patch("src.campaigns.chain_consent_service.put_upload")
    @patch(
        "src.campaigns.chain_consent_service.write_consent_document",
        return_value=hashlib.sha256(b"local-consent").hexdigest(),
    )
    def test_uploaded_document_hash_mismatch_is_recorded(
        self,
        _mock_write,
        _mock_upload,
        _mock_get_bytes,
    ) -> None:
        record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-materials",
            edge_id="edge-materials",
            token=self.token,
            evidence={"job_id": self.campaign["job_id"]},
        )

        with self.assertRaises(MaterialsConsentDocumentError):
            ensure_materials_request_document(self.token)

        with session_scope() as session:
            event = session.scalar(
                select(CampaignChainConsentEvent).where(
                    CampaignChainConsentEvent.token == self.token
                )
            )
            assert event is not None
            self.assertEqual(event.document_status, "error")
            self.assertIn("SHA-256", event.document_error or "")

    @patch("src.campaigns.chain_consent_service.put_upload", side_effect=RuntimeError("S3 unavailable"))
    @patch(
        "src.campaigns.chain_consent_service.write_consent_document",
        return_value="b" * 64,
    )
    def test_document_upload_error_is_recorded_and_can_be_retried(
        self,
        _mock_write,
        _mock_upload,
    ) -> None:
        record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=self.recipient_id,
            email=self.email,
            node_id="node-materials",
            edge_id="edge-materials",
            token=self.token,
            evidence={"job_id": self.campaign["job_id"]},
        )

        with self.assertRaises(MaterialsConsentDocumentError):
            ensure_materials_request_document(self.token)

        with session_scope() as session:
            event = session.scalar(
                select(CampaignChainConsentEvent).where(
                    CampaignChainConsentEvent.token == self.token
                )
            )
            assert event is not None
            self.assertEqual(event.document_status, "error")
            self.assertIn("S3 unavailable", event.document_error or "")

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
