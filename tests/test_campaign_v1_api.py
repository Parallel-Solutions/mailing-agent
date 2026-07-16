from __future__ import annotations

import unittest
import uuid

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class CampaignV1ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.username = f"c{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        app = FastAPI()
        app.include_router(create_v1_router(check_auth=lambda: Principal(self.username, "t1", "user")))
        self.client = TestClient(app)

    def test_create_update_schedule_launch_pause(self) -> None:
        created = self.client.post("/api/v1/campaigns", json={"name": "API Campaign", "mail_subject": "Hello"})
        self.assertEqual(created.status_code, 200)
        campaign_id = created.json()["result"]["id"]

        patched = self.client.patch(
            f"/api/v1/campaigns/{campaign_id}",
            json={"mail_subject": "Hello 2", "document_mode": "kp"},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["result"]["mail_subject"], "Hello 2")

        recipients = self.client.put(
            f"/api/v1/campaigns/{campaign_id}/recipients",
            json={
                "recipients": [
                    {"company": "A", "contact_name": "A", "email": "a@example.com"},
                    {"company": "B", "contact_name": "B", "email": "b@example.com"},
                ]
            },
        )
        self.assertEqual(recipients.status_code, 200)
        self.assertEqual(recipients.json()["result"]["total"], 2)

        from src.generator.delivery.smtp_mailboxes import create_mailbox

        mailbox = create_mailbox(
            owner_username=self.username,
            provider="custom",
            email="sender@mailpit.local",
            password="x",
            host="mailpit",
            port=1025,
            use_ssl=False,
            use_starttls=False,
            make_default=True,
        )
        self.client.patch(
            f"/api/v1/campaigns/{campaign_id}",
            json={"smtp_mailbox_id": mailbox["id"], "transport": "smtp"},
        )

        schedule = self.client.put(
            f"/api/v1/campaigns/{campaign_id}/schedule",
            json={"send_immediately": True, "batch_size": 1, "interval_seconds": 60},
        )
        self.assertEqual(schedule.status_code, 200)
        self.assertGreaterEqual(schedule.json()["result"]["preview"]["batch_count"], 1)

        launched = self.client.post(f"/api/v1/campaigns/{campaign_id}/launch?force_now=true")
        self.assertEqual(launched.status_code, 200, launched.text)
        self.assertGreaterEqual(len(launched.json()["result"]["batches"]), 1)

        paused = self.client.post(f"/api/v1/campaigns/{campaign_id}/pause")
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["result"]["status"], "paused")


if __name__ == "__main__":
    unittest.main()
