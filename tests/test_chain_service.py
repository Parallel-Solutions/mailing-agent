from __future__ import annotations

import unittest
import uuid

from src.campaigns.chain_service import (
    create_chain,
    empty_chain,
    load_chain,
    normalize_chain,
    publish_chain,
    publish_email_chain,
    record_branch_click,
    resolve_button_label,
    save_chain,
    save_email_chain,
    validate_chain,
)
from tests.bootstrap import bootstrap_test_runtime


class ChainServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.security.user_store import create_user
        from src.campaigns.service import create_campaign

        self.username = f"chain{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "Chain test"})

    def test_validate_detects_cycle_and_unreachable(self) -> None:
        chain = empty_chain()
        root = chain["root_node_id"]
        n2 = "node-2"
        n3 = "node-3"
        chain["nodes"].extend(
            [
                {"id": n2, "name": "Письмо 2", "email_template_id": "t1", "document_template_ids": []},
                {"id": n3, "name": "Письмо 3", "email_template_id": "t1", "document_template_ids": []},
            ]
        )
        chain["edges"] = [
            {"id": "e1", "source_id": root, "target_id": n2, "button_label": "A"},
            {"id": "e2", "source_id": n2, "target_id": n3, "button_label": "B"},
            {"id": "e3", "source_id": n3, "target_id": n2, "button_label": "Loop"},
        ]
        result = validate_chain(chain, strict=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("цикл" in err.lower() for err in result["errors"]))

    def test_save_and_publish_chain(self) -> None:
        chain = empty_chain()
        save_email_chain(self.campaign["id"], self.username, chain)
        with self.assertRaises(ValueError):
            publish_email_chain(self.campaign["id"], self.username)
        chain["nodes"][0]["email_template_id"] = "tmpl-1"
        save_email_chain(self.campaign["id"], self.username, chain)
        publish = publish_email_chain(self.campaign["id"], self.username)
        self.assertTrue(publish["published"])

    def test_normalize_chain_button_label_from_target_block_name(self) -> None:
        chain = empty_chain()
        root = chain["root_node_id"]
        n2 = "node-2"
        chain["nodes"].append(
            {"id": n2, "name": "Получить КП", "email_template_id": "t1", "document_template_ids": []},
        )
        chain["edges"] = [
            {"id": "e1", "source_id": root, "target_id": n2, "button_label": "??, broken"},
        ]
        normalized = normalize_chain(chain)
        self.assertEqual(normalized["edges"][0]["button_label"], "Получить КП")

    def test_validate_strict_requires_target_block_name(self) -> None:
        chain = empty_chain()
        root = chain["root_node_id"]
        n2 = "node-2"
        chain["nodes"].append(
            {"id": n2, "name": "   ", "email_template_id": "t1", "document_template_ids": []},
        )
        chain["edges"] = [
            {"id": "e1", "source_id": root, "target_id": n2, "button_label": "ignored"},
        ]
        chain["nodes"][0]["email_template_id"] = "t1"
        result = validate_chain(chain, strict=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("название" in err.lower() for err in result["errors"]))

    def test_resolve_button_label_prefers_target_name(self) -> None:
        node_by_id = {"node-2": {"id": "node-2", "name": "Follow-up"}}
        label = resolve_button_label(
            {"target_id": "node-2", "button_label": "??, broken"},
            node_by_id,
        )
        self.assertEqual(label, "Follow-up")

    def test_record_branch_click_idempotent(self) -> None:
        from src.infra.db import session_scope
        from src.infra.models import CampaignChainToken, CampaignRecipient

        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = "tmpl-1"
        save_email_chain(self.campaign["id"], self.username, chain)
        with session_scope() as session:
            recipient = CampaignRecipient(
                campaign_id=self.campaign["id"],
                row_index=0,
                company="A",
                contact_name="B",
                email="chain@example.com",
            )
            session.add(recipient)
            session.flush()
            token = CampaignChainToken(
                token=str(uuid.uuid4()),
                campaign_id=self.campaign["id"],
                recipient_id=recipient.id,
                edge_id="edge-1",
                source_node_id=chain["root_node_id"],
                target_node_id=chain["root_node_id"],
            )
            session.add(token)
            session.flush()
            token_value = token.token
            recipient_id = recipient.id

        first = record_branch_click(token_value)
        second = record_branch_click(token_value)
        self.assertFalse(first["already_clicked"])
        self.assertTrue(second["already_clicked"])
        self.assertEqual(first["recipient_id"], recipient_id)

    def test_normalize_chain_defaults_kind_email(self) -> None:
        chain = empty_chain()
        normalized = normalize_chain(chain)
        self.assertEqual(normalized["version"], 2)
        self.assertEqual(normalized["nodes"][0]["kind"], "email")

    def test_validate_link_node_requires_url(self) -> None:
        chain = empty_chain()
        root = chain["root_node_id"]
        link_id = "node-link"
        chain["nodes"][0]["email_template_id"] = "t1"
        chain["nodes"].append(
            {
                "id": link_id,
                "name": "Сайт",
                "kind": "link",
                "link_kind": "custom",
                "link_url": "",
            }
        )
        chain["edges"] = [
            {"id": "e1", "source_id": root, "target_id": link_id, "button_label": "Сайт"},
        ]
        result = validate_chain(chain, strict=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("url" in err.lower() for err in result["errors"]))

    def test_validate_root_must_be_email(self) -> None:
        chain = empty_chain()
        root = chain["root_node_id"]
        chain["nodes"] = [
            {
                "id": root,
                "name": "Отписаться",
                "kind": "link",
                "link_kind": "unsubscribe",
            }
        ]
        result = validate_chain(chain, strict=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("началь" in err.lower() for err in result["errors"]))

    def test_validate_link_node_cannot_have_children(self) -> None:
        chain = empty_chain()
        root = chain["root_node_id"]
        link_id = "node-link"
        child_id = "node-child"
        chain["nodes"][0]["email_template_id"] = "t1"
        chain["nodes"].extend(
            [
                {
                    "id": link_id,
                    "name": "Отписаться",
                    "kind": "link",
                    "link_kind": "unsubscribe",
                },
                {
                    "id": child_id,
                    "name": "Письмо 2",
                    "kind": "email",
                    "email_template_id": "t1",
                    "document_template_ids": [],
                },
            ]
        )
        chain["edges"] = [
            {"id": "e1", "source_id": root, "target_id": link_id, "button_label": "Отписаться"},
            {"id": "e2", "source_id": link_id, "target_id": child_id, "button_label": "Письмо 2"},
        ]
        result = validate_chain(chain, strict=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("дочерн" in err.lower() for err in result["errors"]))

    def test_publish_chain_with_link_nodes(self) -> None:
        chain = empty_chain()
        root = chain["root_node_id"]
        link_id = "node-link"
        chain["nodes"][0]["email_template_id"] = "tmpl-1"
        chain["nodes"].append(
            {
                "id": link_id,
                "name": "Подписаться",
                "kind": "link",
                "link_kind": "subscribe",
            }
        )
        chain["edges"] = [
            {"id": "e1", "source_id": root, "target_id": link_id, "button_label": "Подписаться"},
        ]
        save_email_chain(self.campaign["id"], self.username, chain)
        publish = publish_email_chain(self.campaign["id"], self.username)
        self.assertTrue(publish["published"])

    def test_standalone_chain_crud(self) -> None:
        created = create_chain(self.username, name="Standalone")
        chain_id = created["id"]
        loaded = load_chain(chain_id, self.username)
        self.assertEqual(loaded["name"], "Standalone")
        self.assertTrue(loaded["chain"]["nodes"])

        chain = loaded["chain"]
        chain["nodes"][0]["email_template_id"] = "tmpl-1"
        saved = save_chain(chain_id, self.username, chain)
        self.assertEqual(saved["chain"]["nodes"][0]["email_template_id"], "tmpl-1")

        published = publish_chain(chain_id, self.username)
        self.assertTrue(published["published"])

    def test_campaign_linked_to_standalone_chain(self) -> None:
        from src.campaigns.service import update_campaign

        created = create_chain(self.username, name="Linked chain")
        chain_id = created["id"]
        updated = update_campaign(
            self.campaign["id"],
            self.username,
            {"email_chain_id": chain_id, "send_scenario": "email_chain"},
        )
        self.assertEqual(updated["email_chain_id"], chain_id)

        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = "tmpl-linked"
        save_email_chain(self.campaign["id"], self.username, chain)
        reloaded = load_chain(chain_id, self.username)
        self.assertEqual(reloaded["chain"]["nodes"][0]["email_template_id"], "tmpl-linked")


if __name__ == "__main__":
    unittest.main()
