from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.campaigns.chain_send_service import run_chain_followup
from src.campaigns.chain_service import LINK_KIND_UNSUBSCRIBE
from src.generator.delivery.suppression_store import is_suppressed
from src.infra.db import session_scope
from src.infra.models import CampaignChainToken, CampaignRecipient
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.chain_router import create_chain_router
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class ChainTestSendApiTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.username = f"cts{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        app = FastAPI()
        app.include_router(create_v1_router(check_auth=lambda: Principal(self.username, "t1", "user")))
        app.include_router(create_chain_router())
        self.client = TestClient(app)

        created = self.client.post("/api/v1/campaigns", json={"name": "Chain test send"})
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

        loaded = self.client.get(f"/api/v1/campaigns/{self.campaign_id}/email-chain")
        self.assertEqual(loaded.status_code, 200)
        chain = loaded.json()["result"]["chain"]
        root = chain["root_node_id"]
        node2 = "node-email-2"
        chain["nodes"][0]["email_template_id"] = self.email_template_id
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
        published = self.client.post(f"/api/v1/campaigns/{self.campaign_id}/email-chain/publish")
        self.assertEqual(published.status_code, 200, published.text)
        self.root_node_id = root
        self.node2_id = node2

        recipients = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/recipients",
            json={
                "recipients": [
                    {
                        "company": "PreviewCo",
                        "contact_name": "Alice",
                        "email": "alice@example.com",
                    },
                ]
            },
        )
        self.assertEqual(recipients.status_code, 200, recipients.text)

        mapping = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/variable-mapping",
            json={"mapping": {"company": "company", "contact_name": "contact_name"}},
        )
        self.assertEqual(mapping.status_code, 200, mapping.text)

        connection = self.client.post(
            "/api/v1/connections",
            json={
                "transport": "rusender",
                "email": "sender@example.com",
                "sender_name": "Sender",
                "api_token": "rs_test_token",
            },
        )
        self.assertEqual(connection.status_code, 200, connection.text)
        connection_id = connection.json()["result"]["id"]
        patched = self.client.patch(
            f"/api/v1/campaigns/{self.campaign_id}",
            json={"smtp_mailbox_id": connection_id, "transport": "rusender"},
        )
        self.assertEqual(patched.status_code, 200, patched.text)

    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="rusender:test-root")
    def test_test_email_starts_chain_test_mode(self, mock_send) -> None:
        test_email = "tester@example.com"
        response = self.client.post(
            f"/api/v1/campaigns/{self.campaign_id}/test-email",
            json={"to_email": test_email},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()["result"]
        self.assertEqual(payload["mode"], "chain_test")
        self.assertEqual(payload["to"], test_email)
        self.assertEqual(payload["node_id"], self.root_node_id)
        self.assertEqual(payload["recipient_preview"]["company"], "PreviewCo")

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["to_email"], test_email)
        self.assertTrue(str(kwargs["subject"]).startswith("[TEST] "))
        self.assertIn("PreviewCo", kwargs["html"])

        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
            assert recipient is not None
            self.assertNotEqual(recipient.send_status, "in_chain")
            self.assertIsNone(dict(recipient.extra or {}).get("chain"))

            tokens = session.scalars(
                select(CampaignChainToken).where(CampaignChainToken.campaign_id == self.campaign_id)
            ).all()
            self.assertEqual(len(tokens), 1)
            self.assertEqual(tokens[0].test_email, test_email)

    @patch("src.campaigns.batch_worker._send_delivery_message")
    def test_followup_sends_to_test_email_not_recipient(self, mock_send) -> None:
        mock_send.side_effect = ["rusender:test-root", "rusender:test-followup"]
        test_email = "tester@example.com"

        started = self.client.post(
            f"/api/v1/campaigns/{self.campaign_id}/test-email",
            json={"to_email": test_email},
        )
        self.assertEqual(started.status_code, 200, started.text)

        with session_scope() as session:
            token_row = session.scalar(
                select(CampaignChainToken).where(CampaignChainToken.campaign_id == self.campaign_id)
            )
            assert token_row is not None
            token = token_row.token

        result = run_chain_followup(
            {
                "token": token,
                "campaign_id": self.campaign_id,
                "recipient_id": started.json()["result"]["recipient_preview"]["id"],
                "target_node_id": self.node2_id,
            }
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(mock_send.call_count, 2)
        followup_kwargs = mock_send.call_args.kwargs
        self.assertEqual(followup_kwargs["to_email"], test_email)
        self.assertTrue(str(followup_kwargs["subject"]).startswith("[TEST] "))
        self.assertIn("PreviewCo", followup_kwargs["html"])

    def test_test_unsubscribe_link_does_not_record_suppression(self) -> None:
        loaded = self.client.get(f"/api/v1/campaigns/{self.campaign_id}/email-chain")
        chain = loaded.json()["result"]["chain"]
        chain["nodes"].append(
            {
                "id": "node-unsub",
                "name": "Отписаться",
                "kind": "link",
                "link_kind": LINK_KIND_UNSUBSCRIBE,
            }
        )
        saved = self.client.put(f"/api/v1/campaigns/{self.campaign_id}/email-chain", json=chain)
        self.assertEqual(saved.status_code, 200, saved.text)

        test_email = "test-unsub@example.com"
        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
            assert recipient is not None
            token = CampaignChainToken(
                token=str(uuid.uuid4()),
                campaign_id=self.campaign_id,
                recipient_id=int(recipient.id),
                edge_id="edge-unsub",
                source_node_id=self.root_node_id,
                target_node_id="node-unsub",
                send_status="pending",
                test_email=test_email,
            )
            session.add(token)
            session.flush()
            token_value = token.token

        response = self.client.get(f"/chain/branch/{token_value}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Вы отписаны", response.text)

        suppressed, _reason = is_suppressed("alice@example.com")
        self.assertFalse(suppressed)

    @patch("src.web.chain_router.dispatch_chain_followup")
    def test_email_branch_click_dispatches_followup(self, mock_dispatch) -> None:
        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
            assert recipient is not None
            token_row = CampaignChainToken(
                token=str(uuid.uuid4()),
                campaign_id=self.campaign_id,
                recipient_id=int(recipient.id),
                edge_id="edge-1",
                source_node_id=self.root_node_id,
                target_node_id=self.node2_id,
                send_status="pending",
            )
            session.add(token_row)
            session.flush()
            token_value = token_row.token

        response = self.client.get(f"/chain/branch/{token_value}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Спасибо", response.text)
        mock_dispatch.assert_called_once_with(token_value)


if __name__ == "__main__":
    unittest.main()
