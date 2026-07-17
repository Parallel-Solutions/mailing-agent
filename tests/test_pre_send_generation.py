from __future__ import annotations

import unittest
import uuid
from io import BytesIO
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from src.campaigns import generation_service
from src.campaigns.service import create_campaign, replace_recipients
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient
from src.jobs.storage import resolve_job_paths
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class PreSendGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"pre{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Pre-send docs"})
        self.campaign_id = self.campaign["id"]
        self.job_id = self.campaign["job_id"]
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[{"company": "A", "contact_name": "A", "email": "a@example.com"}],
        )

        app = FastAPI()
        app.include_router(create_v1_router(check_auth=lambda: Principal(self.username, "t1", "user")))
        client = TestClient(app)
        pdf_buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(pdf_buffer)
        uploaded = client.post(
            "/api/v1/templates/upload",
            data={"template_type": "kp", "name": "KP"},
            files={"file": ("kp.pdf", pdf_buffer.getvalue(), "application/pdf")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.kp_template_id = uploaded.json()["result"]["id"]
        with session_scope() as session:
            camp = session.get(Campaign, self.campaign_id)
            assert camp is not None
            camp.kp_template_id = self.kp_template_id
            camp.document_mode = "kp"
            session.flush()
            self.recipient_id = session.scalar(
                select(CampaignRecipient.id).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
        assert self.recipient_id is not None

    def test_ensure_campaign_workspace_prepares_manifest(self) -> None:
        job_id = generation_service.ensure_campaign_workspace(self.campaign_id, self.username)
        self.assertEqual(job_id, self.job_id)
        manifest = generation_service._load_manifest(self.job_id)
        self.assertEqual(manifest.get("recipient_count"), 1)
        self.assertTrue(resolve_job_paths(self.job_id).data_xlsx.exists())

    def test_ensure_recipient_documents_skips_when_ready(self) -> None:
        generation_service.ensure_campaign_workspace(self.campaign_id, self.username)
        with patch.object(generation_service, "_recipient_documents_ready", return_value=True) as ready:
            with patch(
                "src.generator.generation.generator_agent.run_generator_agent",
            ) as run_generator:
                generation_service.ensure_recipient_documents(
                    campaign_id=self.campaign_id,
                    recipient_id=int(self.recipient_id),
                    owner_username=self.username,
                    job_id=self.job_id,
                    document_mode="kp",
                )
        ready.assert_called_once()
        run_generator.assert_not_called()

    def test_ensure_recipient_documents_runs_generator_when_missing(self) -> None:
        generation_service.ensure_campaign_workspace(self.campaign_id, self.username)
        ready_calls = {"count": 0}

        def ready_side_effect(*args, **kwargs):
            ready_calls["count"] += 1
            return ready_calls["count"] > 1

        with patch.object(generation_service, "_recipient_documents_ready", side_effect=ready_side_effect):
            with patch(
                "src.generator.generation.generator_agent.run_generator_agent",
                return_value={
                    "status": "completed",
                    "results": [{"id": str(self.recipient_id), "status": "ok"}],
                },
            ) as run_generator:
                generation_service.ensure_recipient_documents(
                    campaign_id=self.campaign_id,
                    recipient_id=int(self.recipient_id),
                    owner_username=self.username,
                    job_id=self.job_id,
                    document_mode="kp",
                )
        run_generator.assert_called_once()
        self.assertTrue(run_generator.call_args.kwargs.get("auto_run_philologist"))
