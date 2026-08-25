from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.campaigns.chain_service import (
    LINK_KIND_CUSTOM,
    LINK_KIND_UNSUBSCRIBE,
)
from src.web.chain_router import create_chain_router


class ChainScannerGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(create_chain_router())
        self.client = TestClient(app)

    def test_branch_get_never_records_recipient_action(self) -> None:
        targets = (
            (
                {
                    "id": "next-email",
                    "kind": "email",
                    "consent_on_click": False,
                },
                "Продолжить",
            ),
            (
                {
                    "id": "unsubscribe",
                    "kind": "link",
                    "link_kind": LINK_KIND_UNSUBSCRIBE,
                },
                "Подтвердите отписку",
            ),
            (
                {
                    "id": "custom",
                    "kind": "link",
                    "link_kind": LINK_KIND_CUSTOM,
                    "link_url": "https://t.me/example",
                },
                "Перейти по ссылке",
            ),
        )
        context = {
            "campaign_id": "campaign-1",
            "target_node_id": "target-1",
            "test_email": None,
        }

        for target_node, expected_text in targets:
            with self.subTest(target_node=target_node["id"]), patch(
                "src.web.chain_router.inspect_branch_token",
                return_value=context,
            ), patch(
                "src.web.chain_router._resolve_target_node",
                return_value=target_node,
            ), patch("src.web.chain_router.record_branch_click") as record_click:
                response = self.client.get("/chain/branch/token-1")

                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn(expected_text, response.text)
                self.assertIn('method="post"', response.text)
                record_click.assert_not_called()

    def test_content_and_document_gets_do_not_record_open(self) -> None:
        with patch(
            "src.web.chain_router.inspect_tracked_resource",
            return_value={
                "target_url": "https://example.test/page",
                "test_email": None,
            },
        ), patch(
            "src.web.chain_router.record_tracked_resource_open"
        ) as record_open:
            content_response = self.client.get("/chain/content/content-token")
            document_response = self.client.get("/chain/document/document-token")

        self.assertEqual(content_response.status_code, 200, content_response.text)
        self.assertEqual(document_response.status_code, 200, document_response.text)
        self.assertIn('method="post"', content_response.text)
        self.assertIn('method="post"', document_response.text)
        record_open.assert_not_called()

    def test_content_post_records_and_redirects(self) -> None:
        with patch(
            "src.web.chain_router.record_tracked_resource_open",
            return_value={"target_url": "https://example.test/page"},
        ) as record_open:
            response = self.client.post(
                "/chain/content/content-token",
                follow_redirects=False,
                headers={"user-agent": "human-browser"},
            )

        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(response.headers["location"], "https://example.test/page")
        self.assertEqual(record_open.call_args.kwargs["http_method"], "POST")
        self.assertEqual(
            record_open.call_args.kwargs["user_agent"],
            "human-browser",
        )

    def test_regular_branch_post_records_and_dispatches(self) -> None:
        target_node = {
            "id": "next-email",
            "kind": "email",
            "consent_on_click": False,
        }
        click_result = {
            "campaign_id": "campaign-1",
            "recipient_id": 1,
            "edge_id": "edge-1",
            "target_node_id": "next-email",
            "send_status": "pending",
            "test_email": None,
            "already_clicked": False,
        }
        with patch(
            "src.web.chain_router.record_branch_click",
            return_value=click_result,
        ) as record_click, patch(
            "src.web.chain_router._resolve_target_node",
            return_value=target_node,
        ), patch("src.web.chain_router.dispatch_chain_followup") as dispatch:
            response = self.client.post(
                "/chain/branch/token-1",
                headers={"user-agent": "human-browser"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(record_click.call_args.kwargs["http_method"], "POST")
        dispatch.assert_called_once_with("token-1")


if __name__ == "__main__":
    unittest.main()
