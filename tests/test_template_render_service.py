from __future__ import annotations

import unittest
import uuid
from io import BytesIO
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from src.campaigns import template_render_service
from src.campaigns.service import create_campaign, replace_recipients
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient, MailTemplate
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class TemplateRenderServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"tr{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Render docs"})
        self.campaign_id = self.campaign["id"]
        self.job_id = self.campaign["job_id"]
        replace_recipients(
            self.campaign_id,
            self.username,
            recipients=[{"company": "A", "contact_name": "A", "email": "a@example.com"}],
        )

        app = FastAPI()
        app.include_router(create_v1_router(check_auth=lambda: Principal(self.username, "t1", "user")))
        self.client = TestClient(app)

        pdf_buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(pdf_buffer)
        uploaded = self.client.post(
            "/api/v1/templates/upload",
            data={"template_type": "document", "name": "Doc"},
            files={"file": ("doc.pdf", pdf_buffer.getvalue(), "application/pdf")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.template_id = uploaded.json()["result"]["id"]
        self.client.patch(
            f"/api/v1/templates/{self.template_id}",
            json={"is_template": True},
        )

        with session_scope() as session:
            camp = session.get(Campaign, self.campaign_id)
            assert camp is not None
            camp.draft_payload = {
                "email_chain": {
                    "version": 1,
                    "root_node_id": "n1",
                    "nodes": [
                        {
                            "id": "n1",
                            "name": "Root",
                            "kind": "email",
                            "email_template_id": None,
                            "document_template_ids": [self.template_id],
                        }
                    ],
                    "edges": [],
                }
            }
            session.flush()
            self.recipient_id = session.scalar(
                select(CampaignRecipient.id).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
        assert self.recipient_id is not None

    def test_static_attachment_when_not_template(self) -> None:
        with session_scope() as session:
            tmpl = session.get(MailTemplate, self.template_id)
            assert tmpl is not None
            tmpl.is_template = False
            session.flush()
            recipient = session.get(CampaignRecipient, int(self.recipient_id))
            campaign = session.get(Campaign, self.campaign_id)
            assert recipient is not None and campaign is not None
            session.expunge(recipient)
            session.expunge(campaign)

        filename, data = template_render_service.render_document_template_for_recipient(
            template_id=self.template_id,
            recipient=recipient,
            campaign=campaign,
            job_id=self.job_id,
        )
        self.assertTrue(filename)
        self.assertTrue(data)

    def test_personalized_pdf_is_cached(self) -> None:
        with session_scope() as session:
            recipient = session.get(CampaignRecipient, int(self.recipient_id))
            campaign = session.get(Campaign, self.campaign_id)
            assert recipient is not None and campaign is not None
            session.expunge(recipient)
            session.expunge(campaign)

        template_render_service.render_document_template_for_recipient(
            template_id=self.template_id,
            recipient=recipient,
            campaign=campaign,
            job_id=self.job_id,
        )
        cache_path = template_render_service._cache_path(
            self.job_id,
            int(self.recipient_id),
            self.template_id,
            ".pdf",
        )
        self.assertTrue(cache_path.exists())

        with patch(
            "src.campaigns.template_render_service.get_bytes",
            side_effect=AssertionError("should use cache"),
        ):
            template_render_service.render_document_template_for_recipient(
                template_id=self.template_id,
                recipient=recipient,
                campaign=campaign,
                job_id=self.job_id,
            )

    def test_pre_generate_batch_creates_manifest(self) -> None:
        result = template_render_service.pre_generate_batch_templates(
            campaign_id=self.campaign_id,
            recipient_ids=[int(self.recipient_id)],
        )
        self.assertIn("template_ids", result)
        manifest = template_render_service.load_manifest(self.job_id)
        self.assertEqual(manifest.get("template_ids"), [self.template_id])

    def test_launch_schedules_pre_generate_task(self) -> None:
        from cryptography.fernet import Fernet
        from src.generator.delivery.smtp_mailboxes import create_mailbox
        from src.infra.models import BackgroundTask
        from src.utils.config import settings

        with patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii")):
            mailbox = create_mailbox(
                owner_username=self.username,
                provider="custom",
                email="sender@example.com",
                password="x",
                host="mailpit",
                port=1025,
                use_ssl=False,
                use_starttls=False,
                make_default=True,
            )
            self.client.patch(
                f"/api/v1/campaigns/{self.campaign_id}",
                json={
                    "smtp_mailbox_id": mailbox["id"],
                    "transport": "smtp",
                    "mail_subject": "Hello",
                },
            )
            self.client.put(
                f"/api/v1/campaigns/{self.campaign_id}/schedule",
                json={"send_immediately": True, "batch_size": 1, "interval_seconds": 60},
            )
            launched = self.client.post(f"/api/v1/campaigns/{self.campaign_id}/launch?force_now=true")
            self.assertEqual(launched.status_code, 200, launched.text)

        from sqlalchemy import func

        with session_scope() as session:
            count = session.scalar(
                select(func.count())
                .select_from(BackgroundTask)
                .where(
                    BackgroundTask.task_type == "campaign_pre_generate",
                    BackgroundTask.payload["campaign_id"].as_string() == self.campaign_id,
                )
            )
        self.assertGreaterEqual(int(count or 0), 1)
