from __future__ import annotations

import unittest
import uuid
from pathlib import Path

import fitz
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from src.campaigns.pdf_overlay_service import FONT_FILES, PDF_AUTO_LAYOUT_VERSION
from src.infra.db import session_scope
from src.infra.models import MailTemplate, TemplateVersion
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


def _layout_pdf() -> bytes:
    font_file = next((path for path in FONT_FILES if Path(path).exists()), None)
    if font_file is None:
        raise unittest.SkipTest("A Unicode TTF font is required for the PDF layout fixture")
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    font = {"fontname": "LayoutReviewSans", "fontfile": font_file}
    page.insert_text(
        fitz.Point(65, 100),
        "№ {{id}}-ИП от {{current_date}}",
        fontsize=11,
        **font,
    )
    page.insert_text(fitz.Point(360, 100), "ADM_NAME", fontsize=11, **font)
    page.insert_text(fitz.Point(360, 165), "Следующий блок", fontsize=11, **font)
    page.insert_text(
        fitz.Point(210, 205),
        "Уважаемый {{contact_name}}!",
        fontsize=11,
        **font,
    )
    data = document.tobytes()
    document.close()
    return data


class DocumentLayoutReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"dl{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        app = FastAPI()
        app.include_router(
            create_v1_router(
                check_auth=lambda: Principal(self.username, "t1", "user"),
            )
        )
        self.client = TestClient(app)

        campaign = self.client.post(
            "/api/v1/campaigns",
            json={"name": "Layout review"},
        )
        self.assertEqual(campaign.status_code, 200, campaign.text)
        self.campaign_id = campaign.json()["result"]["id"]

        recipients = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/recipients",
            json={
                "recipients": [
                    {
                        "company": (
                            'Администрация муниципального образования '
                            '"Энемское городское поселение"'
                        ),
                        "contact_name": "Заурдин Джабраилович",
                        "email": "preview@example.com",
                    }
                ]
            },
        )
        self.assertEqual(recipients.status_code, 200, recipients.text)

        uploaded = self.client.post(
            "/api/v1/templates/upload",
            data={"template_type": "document", "name": "Layout PDF"},
            files={"file": ("layout.pdf", _layout_pdf(), "application/pdf")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.template_id = uploaded.json()["result"]["id"]
        patched = self.client.patch(
            f"/api/v1/templates/{self.template_id}",
            json={"is_template": True},
        )
        self.assertEqual(patched.status_code, 200, patched.text)

        chain_response = self.client.get(
            f"/api/v1/campaigns/{self.campaign_id}/email-chain"
        )
        chain = chain_response.json()["result"]["chain"]
        chain["nodes"][0]["document_template_ids"] = [self.template_id]
        saved = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/email-chain",
            json=chain,
        )
        self.assertEqual(saved.status_code, 200, saved.text)

    def test_inspect_and_apply_create_a_reversible_template_version(self) -> None:
        inspected = self.client.post(
            f"/api/v1/campaigns/{self.campaign_id}/document-layout/inspect"
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        payload = inspected.json()["result"]
        self.assertEqual(payload["recipient"]["contact_name"], "Заурдин Джабраилович")
        self.assertEqual(len(payload["documents"]), 1)
        document = payload["documents"][0]
        self.assertEqual(document["status"], "candidate")
        self.assertTrue(document["can_apply"])
        self.assertTrue(document["before_image"].startswith("data:image/png;base64,"))
        self.assertTrue(document["after_image"].startswith("data:image/png;base64,"))
        self.assertGreaterEqual(len(document["changes"]), 2)

        with session_scope() as session:
            template = session.get(MailTemplate, self.template_id)
            assert template is not None
            original_version_id = template.active_version_id

        applied = self.client.post(
            f"/api/v1/campaigns/{self.campaign_id}/document-layout/apply",
            json={"template_id": self.template_id},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        applied_payload = applied.json()["result"]
        self.assertEqual(applied_payload["layout_version"], PDF_AUTO_LAYOUT_VERSION)
        self.assertNotEqual(applied_payload["template_version_id"], original_version_id)

        with session_scope() as session:
            template = session.get(MailTemplate, self.template_id)
            assert template is not None and template.active_version_id
            active = session.get(TemplateVersion, template.active_version_id)
            assert active is not None
            self.assertEqual(
                (active.editor_state or {}).get("auto_layout", {}).get("version"),
                PDF_AUTO_LAYOUT_VERSION,
            )
            version_count = session.scalar(
                select(func.count())
                .select_from(TemplateVersion)
                .where(TemplateVersion.template_id == self.template_id)
            )
            self.assertEqual(version_count, 2)

        inspected_again = self.client.post(
            f"/api/v1/campaigns/{self.campaign_id}/document-layout/inspect"
        )
        self.assertEqual(inspected_again.status_code, 200, inspected_again.text)
        applied_document = inspected_again.json()["result"]["documents"][0]
        self.assertEqual(applied_document["status"], "already_applied")
        self.assertFalse(applied_document["can_apply"])
