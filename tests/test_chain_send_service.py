from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from src.campaigns.chain_send_service import send_chain_node_email
from src.campaigns.chain_service import empty_chain, save_email_chain
from src.infra.db import session_scope
from src.infra.models import Campaign, CampaignRecipient
from tests.bootstrap import bootstrap_test_runtime


class ChainSendServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.security.user_store import create_user
        from src.campaigns.service import create_campaign

        self.username = f"chainsend{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Chain send test"})
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
        )

        self.assertEqual(result["status"], "sent")
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["send_mode"], "chain_followup")
        self.assertEqual(kwargs["send_run_id"], followup_token)

    @patch("src.campaigns.batch_worker._send_delivery_message", return_value="rusender:uuid-root")
    def test_root_send_uses_default_idempotency_context(self, mock_send) -> None:
        result = send_chain_node_email(
            campaign_id=self.campaign["id"],
            recipient_id=self.recipient_id,
            node_id=self.root_node_id,
        )

        self.assertEqual(result["status"], "sent")
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertIsNone(kwargs["send_mode"])
        self.assertIsNone(kwargs["send_run_id"])

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
            )

        mock_send.assert_not_called()
        with session_scope() as session:
            recipient = session.get(CampaignRecipient, self.recipient_id)
            assert recipient is not None
            self.assertEqual(recipient.send_status, "failed")
            self.assertEqual(recipient.last_error, "layout failed")
            self.assertEqual(dict(recipient.extra or {}).get("layout_error_code"), "kp_font_compact")
