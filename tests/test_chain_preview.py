from __future__ import annotations

import unittest
import uuid
from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class ChainPreviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"cp{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        app = FastAPI()
        app.include_router(create_v1_router(check_auth=lambda: Principal(self.username, "t1", "user")))
        self.client = TestClient(app)

        created = self.client.post("/api/v1/campaigns", json={"name": "Chain preview"})
        self.assertEqual(created.status_code, 200)
        self.campaign_id = created.json()["result"]["id"]

        email = self.client.post(
            "/api/v1/templates",
            json={
                "name": "Email 1",
                "template_type": "email",
                "subject": "Hello {{company}}",
                "body_html": (
                    '<p>Dear {{contact_name}} from {{company}}</p>'
                    '<div data-ma-chain-buttons="1" style="text-align:center;padding:8px 0"></div>'
                ),
            },
        )
        self.assertEqual(email.status_code, 200, email.text)
        self.email_template_id = email.json()["result"]["id"]

        email2 = self.client.post(
            "/api/v1/templates",
            json={
                "name": "Email 2",
                "template_type": "email",
                "subject": "Follow-up {{company}}",
                "body_html": "<p>Second mail for {{company}}</p>",
            },
        )
        self.assertEqual(email2.status_code, 200, email2.text)
        self.email_template_id_2 = email2.json()["result"]["id"]

        pdf_buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(pdf_buffer)
        doc = self.client.post(
            "/api/v1/templates/upload",
            data={"template_type": "document", "name": "Doc"},
            files={"file": ("doc.pdf", pdf_buffer.getvalue(), "application/pdf")},
        )
        self.assertEqual(doc.status_code, 200, doc.text)
        self.document_template_id = doc.json()["result"]["id"]
        self.client.patch(
            f"/api/v1/templates/{self.document_template_id}",
            json={"is_template": False},
        )

        loaded = self.client.get(f"/api/v1/campaigns/{self.campaign_id}/email-chain")
        self.assertEqual(loaded.status_code, 200)
        chain = loaded.json()["result"]["chain"]
        root = chain["root_node_id"]
        node2 = "node-email-2"
        chain["nodes"][0]["email_template_id"] = self.email_template_id
        chain["nodes"][0]["document_template_ids"] = [self.document_template_id]
        chain["nodes"].append(
            {
                "id": node2,
                "name": "Письмо 2",
                "kind": "email",
                "email_template_id": self.email_template_id_2,
                "document_template_ids": [],
            }
        )
        chain["edges"] = [
            {
                "id": "edge-1",
                "source_id": root,
                "target_id": node2,
                "button_label": "Далее",
            }
        ]
        saved = self.client.put(f"/api/v1/campaigns/{self.campaign_id}/email-chain", json=chain)
        self.assertEqual(saved.status_code, 200, saved.text)

        recipients = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/recipients",
            json={
                "recipients": [
                    {
                        "company": "PreviewCo",
                        "contact_name": "Alice",
                        "email": "alice@example.com",
                    },
                    {
                        "company": "OtherCo",
                        "contact_name": "Bob",
                        "email": "bob@example.com",
                    },
                ]
            },
        )
        self.assertEqual(recipients.status_code, 200, recipients.text)

    def test_preview_uses_first_recipient_and_all_email_nodes(self) -> None:
        preview = self.client.post(f"/api/v1/campaigns/{self.campaign_id}/email-chain/preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        payload = preview.json()["result"]

        self.assertEqual(payload["recipient"]["company"], "PreviewCo")
        self.assertEqual(payload["recipient"]["contact_name"], "Alice")
        self.assertEqual(len(payload["items"]), 2)

        root_item = payload["items"][0]
        self.assertIn("PreviewCo", root_item["subject"])
        self.assertIn("PreviewCo", root_item["body_html"])
        self.assertIn("Alice", root_item["body_html"])
        self.assertIn("Письмо 2", root_item["body_html"])
        self.assertNotIn("Вариант 1", root_item["body_html"])

        follow_item = payload["items"][1]
        self.assertIn("PreviewCo", follow_item["subject"])
        self.assertIn("PreviewCo", follow_item["body_html"])

        attachments = root_item["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertTrue(attachments[0]["has_content"])
        self.assertTrue(attachments[0]["filename"])

    def test_preview_attachment_download(self) -> None:
        preview = self.client.post(f"/api/v1/campaigns/{self.campaign_id}/email-chain/preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        payload = preview.json()["result"]
        recipient_id = payload["recipient"]["id"]
        template_id = payload["items"][0]["attachments"][0]["template_id"]

        attachment = self.client.get(
            f"/api/v1/campaigns/{self.campaign_id}/email-chain/preview/attachment",
            params={"recipient_id": recipient_id, "template_id": template_id},
        )
        self.assertEqual(attachment.status_code, 200, attachment.text)
        self.assertTrue(attachment.content)

    def test_preview_without_recipients_returns_400(self) -> None:
        empty = self.client.post("/api/v1/campaigns", json={"name": "Empty"})
        campaign_id = empty.json()["result"]["id"]
        preview = self.client.post(f"/api/v1/campaigns/{campaign_id}/email-chain/preview")
        self.assertEqual(preview.status_code, 400, preview.text)
