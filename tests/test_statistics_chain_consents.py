from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sqlalchemy import select

from src.campaigns.chain_consent_service import (
    record_materials_request,
    record_subscribe,
    record_unsubscribe,
)
from src.campaigns.service import create_campaign, replace_recipients
from src.campaigns.suppression_service import apply_global_email_suppression
from src.generator.delivery.chain_consent_stats import (
    ChainConsentStatsContext,
    build_chain_subscribes_view,
    build_unsubscribes_view,
)
from src.generator.delivery.manager_stats import StatsFilters, build_consents_view
from src.infra.db import session_scope
from src.infra.models import CampaignChainConsentEvent, CampaignRecipient
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.statistics_router import create_statistics_router
from tests.bootstrap import bootstrap_test_runtime


class ChainConsentStatisticsTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"st{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Stats campaign"})
        self.campaign_id = self.campaign["id"]
        self.job_id = self.campaign["job_id"]
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[
                {
                    "company": "Org Stats",
                    "contact_name": "Manager",
                    "email": "sub@example.com",
                },
                {
                    "company": "Org Unsub",
                    "contact_name": "User",
                    "email": "unsub@example.com",
                },
                {
                    "company": "Org Materials",
                    "contact_name": "Director",
                    "email": "materials@example.com",
                },
            ],
        )
        with session_scope() as session:
            recipients = session.scalars(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == self.campaign_id)
            ).all()
            by_email = {row.email: int(row.id) for row in recipients}
        record_subscribe(
            campaign_id=self.campaign_id,
            recipient_id=by_email["sub@example.com"],
            email="sub@example.com",
            node_id="node-sub",
            edge_id="edge-sub",
            token=str(uuid.uuid4()),
        )
        record_unsubscribe(
            campaign_id=self.campaign_id,
            recipient_id=by_email["unsub@example.com"],
            email="unsub@example.com",
            node_id="node-unsub",
            edge_id="edge-unsub",
            token=str(uuid.uuid4()),
        )
        self.materials_token = str(uuid.uuid4())
        record_materials_request(
            campaign_id=self.campaign_id,
            recipient_id=by_email["materials@example.com"],
            email="materials@example.com",
            node_id="node-materials",
            edge_id="edge-materials",
            token=self.materials_token,
            ip="203.0.113.42",
            user_agent="Test Browser",
            evidence={
                "job_id": self.job_id,
                "material_names": ["КП МНГП"],
            },
        )
        with session_scope() as session:
            event = session.scalar(
                select(CampaignChainConsentEvent).where(
                    CampaignChainConsentEvent.token == self.materials_token
                )
            )
            assert event is not None
            event.consent_document_path = "consents/2026-08-17/chain-test_consent.docx"
            event.consent_document_sha256 = "a" * 64
            event.document_status = "ready"
            self.materials_event_id = event.id
        apply_global_email_suppression("manual@example.com", reason="unsubscribe", source="manual")
        self.ctx = ChainConsentStatsContext(
            filters=StatsFilters(job_ids=(self.job_id,)),
            owner_username=self.username,
            is_admin=False,
        )

    def test_build_chain_subscribes_view(self) -> None:
        result = build_chain_subscribes_view(self.ctx, page=1, per_page=10)
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["email"], "sub@example.com")
        self.assertEqual(item["organization"], "Org Stats")

    def test_build_unsubscribes_view(self) -> None:
        result = build_unsubscribes_view(self.ctx, page=1, per_page=10)
        emails = {row["email"] for row in result["items"]}
        self.assertIn("unsub@example.com", emails)
        self.assertGreaterEqual(result["summary"]["total"], 1)

    def test_materials_request_appears_in_consents_with_document(self) -> None:
        result = build_consents_view(StatsFilters(job_ids=(self.job_id,)))
        item = next(
            row for row in result["items"] if row["email"] == "materials@example.com"
        )
        self.assertEqual(item["consent_status_label"], "Подтверждено получение материалов")
        self.assertEqual(item["materials_label"], "КП МНГП")
        self.assertEqual(item["confirmed_ip"], "203.0.113.42")
        self.assertEqual(
            item["consent_document_url"],
            f"/api/sender/consents/{self.materials_event_id}/document",
        )

    def test_owner_can_download_materials_consent_document(self) -> None:
        app = FastAPI()
        app.include_router(
            create_statistics_router(
                check_auth=lambda: Principal(self.username, "t1", "user"),
                jobs_dir=Path("."),
                resolve_job_paths=lambda _job_id: None,
                logger=__import__("logging").getLogger("test"),
            )
        )
        client = TestClient(app)
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "consent.docx"
            path.write_bytes(b"test-docx")
            with patch(
                "src.web.statistics_router.ensure_local_file",
                return_value=path,
            ):
                response = client.get(
                    f"/api/sender/consents/{self.materials_event_id}/document"
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"test-docx")
        self.assertIn("attachment", response.headers.get("content-disposition", ""))

    def test_statistics_router_exposes_new_endpoints(self) -> None:
        app = FastAPI()
        app.include_router(
            create_statistics_router(
                check_auth=lambda: Principal(self.username, "t1", "user"),
                jobs_dir=__import__("pathlib").Path("."),
                resolve_job_paths=lambda _job_id: None,
                logger=__import__("logging").getLogger("test"),
            )
        )
        client = TestClient(app)

        subscribes = client.get("/api/sender/chain-subscribes", params={"campaign": self.job_id})
        self.assertEqual(subscribes.status_code, 200)
        self.assertEqual(subscribes.json()["result"]["summary"]["total"], 1)

        unsubscribes = client.get("/api/sender/unsubscribes", params={"campaign": self.job_id})
        self.assertEqual(unsubscribes.status_code, 200)
        self.assertGreaterEqual(unsubscribes.json()["result"]["summary"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
