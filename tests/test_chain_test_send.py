from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.campaigns.chain_consent_service import MaterialsConsentDocumentError
from src.campaigns.chain_send_service import run_chain_followup
from src.campaigns.chain_service import (
    LINK_KIND_CUSTOM,
    LINK_KIND_SUBSCRIBE,
    LINK_KIND_UNSUBSCRIBE,
    TRACKED_CONTENT_EDGE_PREFIX,
    TRACKED_DOCUMENT_EDGE_PREFIX,
)
from src.generator.delivery.suppression_store import is_suppressed
from src.infra.db import session_scope
from src.infra.models import (
    CampaignChainConsentEvent,
    CampaignChainToken,
    CampaignRecipient,
)
from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.chain_router import _is_duplicate_consent_token, create_chain_router
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

        created_chain = self.client.post(
            "/api/v1/chains",
            json={"name": "Chain test send"},
        )
        self.assertEqual(created_chain.status_code, 200, created_chain.text)
        self.chain_id = created_chain.json()["result"]["id"]
        linked = self.client.patch(
            f"/api/v1/campaigns/{self.campaign_id}",
            json={
                "send_scenario": "email_chain",
                "email_chain_id": self.chain_id,
            },
        )
        self.assertEqual(linked.status_code, 200, linked.text)

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
                "sending_key_id": 123,
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

        with session_scope() as session:
            stored_token = session.get(CampaignChainToken, token_value)
            assert stored_token is not None
            self.assertEqual(stored_token.clicked_ip, "testclient")
            self.assertTrue(stored_token.clicked_user_agent)
            self.assertEqual(stored_token.clicked_http_method, "GET")

        suppressed, _reason = is_suppressed("alice@example.com")
        self.assertFalse(suppressed)

    def test_recipient_unsubscribe_get_requires_post_confirmation(self) -> None:
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
        saved = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/email-chain",
            json=chain,
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == self.campaign_id
                )
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
            )
            session.add(token)
            session.flush()
            token_value = token.token

        landing = self.client.get(f"/chain/branch/{token_value}")
        self.assertEqual(landing.status_code, 200, landing.text)
        self.assertIn("Подтвердите отписку", landing.text)
        suppressed, _reason = is_suppressed("alice@example.com")
        self.assertFalse(suppressed)
        with session_scope() as session:
            stored = session.get(CampaignChainToken, token_value)
            assert stored is not None
            self.assertIsNone(stored.clicked_at)

        response = self.client.post(f"/chain/branch/{token_value}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Вы отписаны", response.text)
        suppressed, _reason = is_suppressed("alice@example.com")
        self.assertTrue(suppressed)

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

        landing = self.client.get(f"/chain/branch/{token_value}")
        self.assertEqual(landing.status_code, 200, landing.text)
        self.assertIn("Продолжить", landing.text)
        mock_dispatch.assert_not_called()
        with session_scope() as session:
            stored = session.get(CampaignChainToken, token_value)
            assert stored is not None
            self.assertIsNone(stored.clicked_at)

        response = self.client.post(f"/chain/branch/{token_value}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Спасибо", response.text)
        mock_dispatch.assert_called_once_with(token_value)
        with session_scope() as session:
            stored = session.get(CampaignChainToken, token_value)
            assert stored is not None
            self.assertEqual(stored.clicked_http_method, "POST")

    def test_custom_link_get_requires_post_before_redirect(self) -> None:
        loaded = self.client.get(f"/api/v1/campaigns/{self.campaign_id}/email-chain")
        chain = loaded.json()["result"]["chain"]
        chain["nodes"].append(
            {
                "id": "node-custom",
                "name": "Telegram",
                "kind": "link",
                "link_kind": LINK_KIND_CUSTOM,
                "link_url": "https://t.me/example",
            }
        )
        saved = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/email-chain",
            json=chain,
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == self.campaign_id
                )
            )
            assert recipient is not None
            token = CampaignChainToken(
                token=str(uuid.uuid4()),
                campaign_id=self.campaign_id,
                recipient_id=int(recipient.id),
                edge_id="edge-custom",
                source_node_id=self.root_node_id,
                target_node_id="node-custom",
                send_status="pending",
            )
            session.add(token)
            session.flush()
            token_value = token.token

        landing = self.client.get(f"/chain/branch/{token_value}")
        self.assertEqual(landing.status_code, 200, landing.text)
        self.assertIn("Перейти по ссылке", landing.text)
        with session_scope() as session:
            stored = session.get(CampaignChainToken, token_value)
            assert stored is not None
            self.assertIsNone(stored.clicked_at)

        response = self.client.post(
            f"/chain/branch/{token_value}",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(response.headers["location"], "https://t.me/example")

    def test_tracked_content_and_document_gets_require_post(self) -> None:
        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == self.campaign_id
                )
            )
            assert recipient is not None
            content_token = CampaignChainToken(
                token=str(uuid.uuid4()),
                campaign_id=self.campaign_id,
                recipient_id=int(recipient.id),
                edge_id=f"{TRACKED_CONTENT_EDGE_PREFIX}external",
                source_node_id=self.root_node_id,
                target_node_id="",
                error="https://example.test/page",
            )
            document_token = CampaignChainToken(
                token=str(uuid.uuid4()),
                campaign_id=self.campaign_id,
                recipient_id=int(recipient.id),
                edge_id=f"{TRACKED_DOCUMENT_EDGE_PREFIX}doc-1",
                source_node_id=self.root_node_id,
                target_node_id="doc-1",
            )
            session.add_all([content_token, document_token])
            session.flush()
            content_token_value = content_token.token
            document_token_value = document_token.token

        content_landing = self.client.get(f"/chain/content/{content_token_value}")
        document_landing = self.client.get(f"/chain/document/{document_token_value}")
        self.assertIn("Перейти по ссылке", content_landing.text)
        self.assertIn("Открыть документ", document_landing.text)
        with session_scope() as session:
            self.assertIsNone(
                session.get(CampaignChainToken, content_token_value).clicked_at
            )
            self.assertIsNone(
                session.get(CampaignChainToken, document_token_value).clicked_at
            )

        content_response = self.client.post(
            f"/chain/content/{content_token_value}",
            follow_redirects=False,
        )
        self.assertEqual(content_response.status_code, 303, content_response.text)
        self.assertEqual(
            content_response.headers["location"],
            "https://example.test/page",
        )
        with patch(
            "src.campaigns.template_render_service.resolve_cached_attachment",
            return_value=("offer.pdf", b"%PDF-test"),
        ):
            document_response = self.client.post(
                f"/chain/document/{document_token_value}"
            )
        self.assertEqual(document_response.status_code, 200, document_response.text)
        self.assertEqual(document_response.content, b"%PDF-test")
        with session_scope() as session:
            stored_content = session.get(CampaignChainToken, content_token_value)
            stored_document = session.get(CampaignChainToken, document_token_value)
            assert stored_content is not None and stored_document is not None
            self.assertEqual(stored_content.clicked_http_method, "POST")
            self.assertEqual(stored_document.clicked_http_method, "POST")

    @patch("src.web.chain_router.dispatch_chain_followup")
    @patch("src.web.chain_router.ensure_materials_request_document")
    def test_email_branch_click_records_materials_request_before_followup(
        self,
        mock_ensure_document,
        mock_dispatch,
    ) -> None:
        loaded = self.client.get(f"/api/v1/campaigns/{self.campaign_id}/email-chain")
        chain = loaded.json()["result"]["chain"]
        target = next(node for node in chain["nodes"] if node["id"] == self.node2_id)
        target["consent_on_click"] = True
        saved = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/email-chain", json=chain
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        saved_target = next(
            node
            for node in saved.json()["result"]["chain"]["nodes"]
            if node["id"] == self.node2_id
        )
        self.assertTrue(saved_target["consent_on_click"])

        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == self.campaign_id
                )
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

        landing = self.client.get(f"/chain/branch/{token_value}")
        self.assertEqual(landing.status_code, 200, landing.text)
        self.assertIn("Подтвердите получение материалов", landing.text)
        self.assertIn('method="post"', landing.text)
        mock_ensure_document.assert_not_called()
        mock_dispatch.assert_not_called()
        with session_scope() as session:
            event = session.scalar(
                select(CampaignChainConsentEvent).where(
                    CampaignChainConsentEvent.token == token_value
                )
            )
            token_row = session.get(CampaignChainToken, token_value)
            self.assertIsNone(event)
            assert token_row is not None
            self.assertIsNone(token_row.clicked_at)
            self.assertIsNone(token_row.clicked_ip)
            self.assertIsNone(token_row.clicked_user_agent)
            self.assertIsNone(token_row.clicked_http_method)

        response = self.client.post(f"/chain/branch/{token_value}")
        self.assertEqual(response.status_code, 200, response.text)
        mock_ensure_document.assert_called_once_with(token_value)
        mock_dispatch.assert_called_once_with(token_value)

        with session_scope() as session:
            event = session.scalar(
                select(CampaignChainConsentEvent).where(
                    CampaignChainConsentEvent.token == token_value
                )
            )
            assert event is not None
            self.assertEqual(event.action, "materials_request")
            self.assertEqual(event.email, "alice@example.com")
            self.assertEqual(event.confirmed_ip, "testclient")
            self.assertTrue(event.confirmed_user_agent)
            self.assertEqual(event.document_status, "pending")
            self.assertEqual(event.evidence_payload["campaign_id"], self.campaign_id)
            self.assertEqual(event.evidence_payload["target_node_name"], "Письмо 2")
            token_row = session.get(CampaignChainToken, token_value)
            assert token_row is not None
            self.assertEqual(token_row.clicked_ip, "testclient")
            self.assertTrue(token_row.clicked_user_agent)
            self.assertEqual(token_row.clicked_http_method, "POST")

    @patch("src.web.chain_router.dispatch_chain_followup")
    @patch(
        "src.web.chain_router.ensure_materials_request_document",
        side_effect=MaterialsConsentDocumentError("storage failed"),
    )
    def test_materials_consent_document_error_blocks_followup(
        self,
        _mock_ensure_document,
        mock_dispatch,
    ) -> None:
        loaded = self.client.get(f"/api/v1/campaigns/{self.campaign_id}/email-chain")
        chain = loaded.json()["result"]["chain"]
        target = next(node for node in chain["nodes"] if node["id"] == self.node2_id)
        target["consent_on_click"] = True
        saved = self.client.put(
            f"/api/v1/campaigns/{self.campaign_id}/email-chain",
            json=chain,
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == self.campaign_id
                )
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

        landing = self.client.get(f"/chain/branch/{token_value}")
        self.assertEqual(landing.status_code, 200, landing.text)
        self.assertIn("Подтвердите получение материалов", landing.text)
        mock_dispatch.assert_not_called()

        response = self.client.post(f"/chain/branch/{token_value}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Не удалось подтвердить запрос", response.text)
        mock_dispatch.assert_not_called()

    @patch("src.web.chain_router.record_subscribe")
    def test_duplicate_subscribe_confirmation_returns_friendly_page_not_500(self, mock_record_subscribe) -> None:
        """A duplicate confirmation conflict must not surface as a 500."""
        from sqlalchemy.exc import IntegrityError

        original = SimpleNamespace(
            diag=SimpleNamespace(constraint_name="idx_chain_consent_token")
        )
        mock_record_subscribe.side_effect = IntegrityError("INSERT", {}, original)

        loaded = self.client.get(f"/api/v1/campaigns/{self.campaign_id}/email-chain")
        chain = loaded.json()["result"]["chain"]
        chain["nodes"].append(
            {
                "id": "node-sub",
                "name": "Подписаться",
                "kind": "link",
                "link_kind": LINK_KIND_SUBSCRIBE,
            }
        )
        saved = self.client.put(f"/api/v1/campaigns/{self.campaign_id}/email-chain", json=chain)
        self.assertEqual(saved.status_code, 200, saved.text)

        with session_scope() as session:
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == self.campaign_id)
            )
            assert recipient is not None
            token = CampaignChainToken(
                token=str(uuid.uuid4()),
                campaign_id=self.campaign_id,
                recipient_id=int(recipient.id),
                edge_id="edge-sub",
                source_node_id=self.root_node_id,
                target_node_id="node-sub",
                send_status="pending",
            )
            session.add(token)
            session.flush()
            token_value = token.token

        landing = self.client.get(f"/chain/branch/{token_value}")
        self.assertEqual(landing.status_code, 200, landing.text)
        self.assertIn("Подтвердите подписку", landing.text)
        mock_record_subscribe.assert_not_called()
        with session_scope() as session:
            token = session.get(CampaignChainToken, token_value)
            assert token is not None
            self.assertIsNone(token.clicked_at)

        response = self.client.post(f"/chain/branch/{token_value}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Спасибо", response.text)

    def test_unrelated_integrity_error_is_not_treated_as_click_race(self) -> None:
        from sqlalchemy.exc import IntegrityError

        original = SimpleNamespace(
            diag=SimpleNamespace(constraint_name="some_other_constraint")
        )
        error = IntegrityError("INSERT", {}, original)

        self.assertFalse(_is_duplicate_consent_token(error))


if __name__ == "__main__":
    unittest.main()
