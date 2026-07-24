from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import select

from src.campaigns.chain_send_service import (
    dispatch_chain_followup,
    prewarm_next_node_documents,
    send_chain_node_email,
)
from src.campaigns.chain_service import empty_chain, save_email_chain
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignChainToken, CampaignRecipient
from tests.bootstrap import bootstrap_test_runtime


class ChainSendServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.security.user_store import create_user
        from src.campaigns.service import create_campaign
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)

        self.username = f"chainsend{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Chain send test"})
        from src.campaigns.connection_service import create_connection
        from src.campaigns.service import update_campaign

        connection = create_connection(
            self.username,
            {
                "transport": "rusender",
                "email": "sender@example.com",
                "sender_name": "Sender",
                "api_token": "rs_test_token",
            },
        )
        update_campaign(
            self.campaign["id"],
            self.username,
            {"connection_ids": [connection["id"]]},
        )
        self.connection_id = connection["id"]
        self.chain = empty_chain()
        self.chain["nodes"][0]["email_template_id"] = "tmpl-root"
        save_email_chain(self.campaign["id"], self.username, self.chain)
        self.root_node_id = self.chain["root_node_id"]

        with session_scope() as session:
            recipient = CampaignRecipient(
                campaign_id=self.campaign["id"],
                row_index=0,
                company="Test Co",
                contact_name="Alex",
                email="chain-send@example.com",
            )
            session.add(recipient)
            session.flush()
            self.recipient_id = int(recipient.id)

    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="rusender:uuid-followup")
    def test_followup_passes_chain_idempotency_context(self, mock_send) -> None:
        followup_token = str(uuid.uuid4())

        result = send_chain_node_email(
            campaign_id=self.campaign["id"],
            recipient_id=self.recipient_id,
            node_id=self.root_node_id,
            followup_token=followup_token,
            connection_id=self.connection_id,
        )

        self.assertEqual(result["status"], "sent")
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["send_mode"], "chain_followup")
        self.assertEqual(kwargs["send_run_id"], followup_token)
        self.assertIs(kwargs["track_links"], False)

    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="rusender:uuid-root")
    def test_root_send_uses_chain_root_send_mode(self, mock_send) -> None:
        result = send_chain_node_email(
            campaign_id=self.campaign["id"],
            recipient_id=self.recipient_id,
            node_id=self.root_node_id,
            connection_id=self.connection_id,
        )

        self.assertEqual(result["status"], "sent")
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["send_mode"], "chain_root")
        self.assertIsNone(kwargs["send_run_id"])
        self.assertIs(kwargs["track_links"], False)

    @patch("src.campaigns.chain_send_service._finalize_chain_send_success")
    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="rusender:uuid-root")
    def test_tokens_persist_when_post_send_finalize_fails(self, mock_send, mock_finalize) -> None:
        node2 = "node-email-2"
        self.chain["nodes"].append(
            {
                "id": node2,
                "name": "Письмо 2",
                "kind": "email",
                "email_template_id": None,
                "document_template_ids": [],
            }
        )
        self.chain["edges"] = [
            {
                "id": "edge-1",
                "source_id": self.root_node_id,
                "target_id": node2,
                "button_label": "Далее",
            }
        ]
        save_email_chain(self.campaign["id"], self.username, self.chain)
        mock_finalize.side_effect = RuntimeError("finalize failed")

        with self.assertRaises(RuntimeError):
            send_chain_node_email(
                campaign_id=self.campaign["id"],
                recipient_id=self.recipient_id,
                node_id=self.root_node_id,
                connection_id=self.connection_id,
            )

        mock_send.assert_called_once()
        with session_scope() as session:
            tokens = session.scalars(
                select(CampaignChainToken).where(CampaignChainToken.campaign_id == self.campaign["id"])
            ).all()
            self.assertEqual(len(tokens), 1)

    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="rusender:uuid-root")
    @patch("src.campaigns.chain_send_service._resolve_document_attachments")
    def test_layout_error_blocks_send_and_records_failure(self, attachments_mock, mock_send) -> None:
        from src.generator.generation.kp_one_page_fitter import KpLayoutError

        attachments_mock.side_effect = KpLayoutError("layout failed", company="Test Co")

        with self.assertRaises(KpLayoutError):
            send_chain_node_email(
                campaign_id=self.campaign["id"],
                recipient_id=self.recipient_id,
                node_id=self.root_node_id,
                batch_id="batch-1",
                connection_id=self.connection_id,
            )

        mock_send.assert_not_called()
        with session_scope() as session:
            recipient = session.get(CampaignRecipient, self.recipient_id)
            assert recipient is not None
            self.assertEqual(recipient.send_status, "failed")
            self.assertEqual(recipient.last_error, "layout failed")
            self.assertEqual(dict(recipient.extra or {}).get("layout_error_code"), "kp_font_compact")


def _immediate_thread(*, target, args=(), daemon=True, name=None):
    target(*args)

    class _Stub:
        def start(self) -> None:
            return None

    return _Stub()


class ChainFollowupDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.security.user_store import create_user
        from src.campaigns.service import create_campaign
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)

        self.username = f"chaindisp{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Chain dispatch test"})
        from src.campaigns.connection_service import create_connection
        from src.campaigns.service import update_campaign

        connection = create_connection(
            self.username,
            {
                "transport": "rusender",
                "email": "sender@example.com",
                "sender_name": "Sender",
                "api_token": "rs_test_token",
            },
        )
        update_campaign(
            self.campaign["id"],
            self.username,
            {"connection_ids": [connection["id"]]},
        )
        self.connection_id = connection["id"]
        self.chain = empty_chain()
        node2 = "node-email-2"
        self.chain["nodes"][0]["email_template_id"] = "tmpl-root"
        self.chain["nodes"].append(
            {
                "id": node2,
                "name": "Письмо 2",
                "kind": "email",
                "email_template_id": "tmpl-followup",
                "document_template_ids": [],
            }
        )
        self.chain["edges"] = [
            {
                "id": "edge-1",
                "source_id": self.chain["root_node_id"],
                "target_id": node2,
                "button_label": "Далее",
            }
        ]
        save_email_chain(self.campaign["id"], self.username, self.chain)
        self.node2_id = node2

        with session_scope() as session:
            recipient = CampaignRecipient(
                campaign_id=self.campaign["id"],
                row_index=0,
                company="Test Co",
                contact_name="Alex",
                email="chain-dispatch@example.com",
            )
            session.add(recipient)
            session.flush()
            self.recipient_id = int(recipient.id)
            token_row = CampaignChainToken(
                token=str(uuid.uuid4()),
                campaign_id=self.campaign["id"],
                recipient_id=self.recipient_id,
                edge_id="edge-1",
                source_node_id=self.chain["root_node_id"],
                target_node_id=node2,
                send_status="pending",
            )
            session.add(token_row)
            session.flush()
            self.token = token_row.token

        with session_scope() as session:
            camp = session.get(Campaign, self.campaign["id"])
            assert camp is not None
            camp.job_id = f"job-{uuid.uuid4().hex[:8]}"
            session.flush()

    @patch("src.campaigns.chain_send_service.threading.Thread", side_effect=_immediate_thread)
    @patch("src.campaigns.chain_send_service.run_chain_followup", return_value={"status": "sent"})
    @patch("src.workers.task_queue.enqueue_task")
    def test_dispatch_uses_fast_path_without_queue(self, mock_enqueue, mock_run, _mock_thread) -> None:
        dispatch_chain_followup(self.token)

        mock_run.assert_called_once()
        mock_enqueue.assert_not_called()
        with session_scope() as session:
            row = session.get(CampaignChainToken, self.token)
            assert row is not None
            self.assertEqual(row.send_status, "sending")

    @patch("src.campaigns.chain_send_service.threading.Thread", side_effect=_immediate_thread)
    @patch("src.campaigns.chain_send_service.run_chain_followup", side_effect=RuntimeError("send failed"))
    @patch("src.workers.task_queue.enqueue_task")
    def test_dispatch_fallback_enqueues_with_priority(self, mock_enqueue, _mock_run, _mock_thread) -> None:
        dispatch_chain_followup(self.token)

        mock_enqueue.assert_called_once()
        self.assertEqual(mock_enqueue.call_args.kwargs.get("priority"), 100)
        self.assertEqual(mock_enqueue.call_args.kwargs.get("task_type"), "chain_followup")
        with session_scope() as session:
            row = session.get(CampaignChainToken, self.token)
            assert row is not None
            self.assertEqual(row.send_status, "pending")

    @patch("src.campaigns.chain_send_service.threading.Thread")
    @patch("src.campaigns.template_render_service.ensure_recipient_templates_rendered")
    def test_prewarm_next_node_documents_renders_target_docs(self, mock_ensure, mock_thread) -> None:
        doc_id = str(uuid.uuid4())
        self.chain["nodes"][1]["document_template_ids"] = [doc_id]
        save_email_chain(self.campaign["id"], self.username, self.chain)

        captured: dict[str, object] = {}

        def _capture_thread(*, target, daemon=True, name=None):
            captured["target"] = target

            class _Stub:
                def start(self) -> None:
                    target()

            return _Stub()

        mock_thread.side_effect = _capture_thread
        prewarm_next_node_documents(
            campaign_id=self.campaign["id"],
            recipient_id=self.recipient_id,
            current_node_id=self.chain["root_node_id"],
        )
        mock_ensure.assert_called_once_with(
            campaign_id=self.campaign["id"],
            recipient_id=self.recipient_id,
            template_ids=[doc_id],
        )
