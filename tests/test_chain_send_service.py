from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import select

from src.campaigns.chain_send_service import (
    dispatch_chain_followup,
    prewarm_next_node_documents,
    send_chain_node_email,
    start_test_chain,
)
from src.campaigns.chain_service import empty_chain, save_email_chain
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignChainToken, CampaignRecipient
from tests.bootstrap import bootstrap_test_runtime


class ChainTestStartTests(unittest.TestCase):
    @patch("src.security.company_access.can_access_owner", return_value=True)
    @patch("src.campaigns.chain_send_service.get_email_chain")
    @patch("src.campaigns.chain_send_service.session_scope")
    @patch("src.campaigns.chain_send_service.send_chain_node_email", return_value={"status": "sent"})
    def test_each_test_chain_start_uses_a_fresh_send_run_id(
        self,
        mock_send,
        mock_session_scope,
        mock_get_chain,
        _mock_access,
    ) -> None:
        session = mock_session_scope.return_value.__enter__.return_value
        session.get.return_value = SimpleNamespace(owner_username="owner", send_scenario="email_chain")
        session.scalar.return_value = SimpleNamespace(
            id=7,
            company="Test Co",
            contact_name="Alex",
            email="source@example.com",
        )
        mock_get_chain.return_value = {"root_node_id": "root-node"}

        for _ in range(2):
            result = start_test_chain(
                "campaign-id",
                "test-recipient@example.com",
                "owner",
                "connection-id",
            )
            self.assertEqual(result["mode"], "chain_test")

        first_run_id = mock_send.call_args_list[0].kwargs["send_run_id"]
        second_run_id = mock_send.call_args_list[1].kwargs["send_run_id"]
        self.assertTrue(first_run_id.startswith("chain-test-"))
        self.assertTrue(second_run_id.startswith("chain-test-"))
        self.assertNotEqual(first_run_id, second_run_id)


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
                "sending_key_id": 123,
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

    @patch(
        "src.campaigns.batch_worker._send_delivery_message",
        side_effect=["rusender:uuid-root", "rusender:uuid-followup"],
    )
    def test_followup_reuses_successful_root_delivery_email(self, mock_send) -> None:
        followup_node_id = "node-email-followup"
        self.chain["nodes"].append(
            {
                "id": followup_node_id,
                "name": "Получить КП",
                "kind": "email",
                "email_template_id": "tmpl-followup",
                "document_template_ids": [],
            }
        )
        self.chain["edges"] = [
            {
                "id": "edge-followup",
                "source_id": self.root_node_id,
                "target_id": followup_node_id,
                "button_label": "Получить КП",
            }
        ]
        save_email_chain(self.campaign["id"], self.username, self.chain)

        root_result = send_chain_node_email(
            campaign_id=self.campaign["id"],
            recipient_id=self.recipient_id,
            node_id=self.root_node_id,
            connection_id=self.connection_id,
        )
        self.assertEqual(root_result["status"], "sent")

        with session_scope() as session:
            recipient = session.get(CampaignRecipient, self.recipient_id)
            assert recipient is not None
            extra = dict(recipient.extra or {})
            self.assertEqual(extra.get("delivery_email"), "chain-send@example.com")
            self.assertIn("chain-send@example.com", list(extra.get("tried_emails") or []))

        followup_result = send_chain_node_email(
            campaign_id=self.campaign["id"],
            recipient_id=self.recipient_id,
            node_id=followup_node_id,
            followup_token=str(uuid.uuid4()),
            connection_id=self.connection_id,
        )

        self.assertEqual(followup_result["status"], "sent")
        self.assertEqual(mock_send.call_count, 2)
        self.assertEqual(mock_send.call_args_list[0].kwargs["to_email"], "chain-send@example.com")
        self.assertEqual(mock_send.call_args_list[1].kwargs["to_email"], "chain-send@example.com")

    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="rusender:uuid-root")
    def test_root_send_uses_chain_root_send_mode(self, mock_send) -> None:
        from src.jobs.job_docs import read_sent_mail_log

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
        sent_log = read_sent_mail_log(self.campaign["job_id"])
        self.assertEqual(sent_log[-1]["chain_node_id"], self.root_node_id)

    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="rusender:uuid-tracked")
    def test_template_links_and_documents_get_personal_tracking_tokens(self, mock_send) -> None:
        from src.campaigns.chain_service import (
            TRACKED_CONTENT_EDGE_PREFIX,
            TRACKED_DOCUMENT_EDGE_PREFIX,
            get_chain_click_stats,
            record_tracked_resource_open,
        )
        from src.campaigns.template_service import create_template

        template = create_template(
            self.username,
            name="Письмо со ссылкой",
            template_type="email",
            subject="Тема",
            body_html='<p><a href="https://inside.example.test/page">Внутренняя ссылка</a></p>',
        )
        self.chain["nodes"][0]["email_template_id"] = template["id"]
        self.chain["nodes"][0]["document_template_ids"] = ["doc-template-1"]
        save_email_chain(self.campaign["id"], self.username, self.chain)

        with patch(
            "src.campaigns.chain_send_service._resolve_document_attachments",
            return_value=(
                [("proposal.pdf", b"%PDF-test")],
                [("doc-template-1", "proposal.pdf")],
            ),
        ):
            send_chain_node_email(
                campaign_id=self.campaign["id"],
                recipient_id=self.recipient_id,
                node_id=self.root_node_id,
                connection_id=self.connection_id,
            )

        html = mock_send.call_args.kwargs["html"]
        self.assertIn("/chain/content/", html)
        self.assertIn("/chain/document/", html)
        with session_scope() as session:
            tokens = session.scalars(
                select(CampaignChainToken).where(
                    CampaignChainToken.campaign_id == self.campaign["id"]
                )
            ).all()
            content_token = next(
                item
                for item in tokens
                if item.edge_id.startswith(TRACKED_CONTENT_EDGE_PREFIX)
            )
            document_token = next(
                item
                for item in tokens
                if item.edge_id.startswith(TRACKED_DOCUMENT_EDGE_PREFIX)
            )

        first_link_open = record_tracked_resource_open(content_token.token, kind="link")
        second_link_open = record_tracked_resource_open(content_token.token, kind="link")
        record_tracked_resource_open(document_token.token, kind="document")
        self.assertFalse(first_link_open["already_opened"])
        self.assertTrue(second_link_open["already_opened"])

        stats = get_chain_click_stats(self.campaign["id"])
        content_link = next(
            item
            for item in stats["steps"][0]["links"]
            if item["kind"] == "template"
        )
        self.assertEqual(content_link["unique_clickers"], 1)
        self.assertEqual(stats["steps"][0]["documents"][0]["unique_openers"], 1)

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
                "sending_key_id": 123,
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

    @patch("src.campaigns.chain_send_service.threading.Thread", side_effect=_immediate_thread)
    @patch(
        "src.campaigns.chain_send_service.run_chain_followup",
        return_value={"status": "skipped", "reason": "invalid_email"},
    )
    @patch("src.workers.task_queue.enqueue_task")
    def test_dispatch_retries_invalid_email_skip(self, mock_enqueue, _mock_run, _mock_thread) -> None:
        dispatch_chain_followup(self.token)

        mock_enqueue.assert_called_once()
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
