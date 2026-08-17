from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from src.campaigns.chain_service import (
    create_chain,
    delete_chain,
    empty_chain,
    get_chain_click_stats,
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

    def test_click_stats_group_links_by_email_chain_step(self) -> None:
        from src.infra.db import session_scope
        from src.infra.models import CampaignChainToken, CampaignRecipient

        chain = empty_chain()
        root = chain["root_node_id"]
        second_node = "node-second"
        link_node = "node-link"
        chain["nodes"][0]["name"] = "Первое письмо"
        chain["nodes"][0]["email_template_id"] = "tmpl-1"
        chain["nodes"].extend(
            [
                {
                    "id": second_node,
                    "name": "Второе письмо",
                    "kind": "email",
                    "email_template_id": "tmpl-2",
                    "document_template_ids": [],
                },
                {
                    "id": link_node,
                    "name": "Перейти на сайт",
                    "kind": "link",
                    "link_kind": "custom",
                    "link_url": "https://example.test/offer",
                },
            ]
        )
        chain["edges"] = [
            {
                "id": "edge-next",
                "source_id": root,
                "target_id": second_node,
                "button_label": "Второе письмо",
            },
            {
                "id": "edge-site",
                "source_id": second_node,
                "target_id": link_node,
                "button_label": "Перейти на сайт",
            },
        ]
        save_email_chain(self.campaign["id"], self.username, chain)

        clicked_at = datetime.now(timezone.utc)
        with session_scope() as session:
            recipient = CampaignRecipient(
                campaign_id=self.campaign["id"],
                row_index=0,
                company="ООО Альфа",
                contact_name="Иван",
                email="ivan@example.test",
            )
            session.add(recipient)
            session.flush()
            session.add_all(
                [
                    CampaignChainToken(
                        token=str(uuid.uuid4()),
                        campaign_id=self.campaign["id"],
                        recipient_id=recipient.id,
                        edge_id="edge-next",
                        source_node_id=root,
                        target_node_id=second_node,
                        clicked_at=clicked_at,
                    ),
                    CampaignChainToken(
                        token=str(uuid.uuid4()),
                        campaign_id=self.campaign["id"],
                        recipient_id=recipient.id,
                        edge_id="edge-site",
                        source_node_id=second_node,
                        target_node_id=link_node,
                        clicked_at=clicked_at,
                    ),
                ]
            )

        stats = get_chain_click_stats(self.campaign["id"])

        self.assertTrue(stats["has_links"])
        self.assertEqual([step["name"] for step in stats["steps"]], ["Первое письмо", "Второе письмо"])
        self.assertEqual(stats["steps"][0]["links"][0]["label"], "Второе письмо")
        self.assertEqual(stats["steps"][1]["links"][0]["url"], "https://example.test/offer")
        self.assertEqual(stats["steps"][1]["links"][0]["unique_clickers"], 1)
        self.assertEqual(
            stats["steps"][1]["links"][0]["clickers"][0]["email"],
            "ivan@example.test",
        )

    def test_click_stats_keep_email_steps_without_links(self) -> None:
        chain = empty_chain()
        chain["nodes"][0]["name"] = "Письмо 1"
        chain["nodes"].append(
            {
                "id": "node-second",
                "name": "Письмо 2",
                "kind": "email",
                "email_template_id": "tmpl-2",
                "document_template_ids": [],
            }
        )
        chain["edges"] = []
        save_email_chain(self.campaign["id"], self.username, chain)

        stats = get_chain_click_stats(self.campaign["id"])

        self.assertFalse(stats["has_links"])
        self.assertEqual(
            [step["name"] for step in stats["steps"]],
            ["Письмо 1", "Письмо 2"],
        )
        self.assertEqual(stats["steps"][1]["links"], [])

    def test_chain_step_analytics_are_attached_per_email_node(self) -> None:
        from src.campaigns.service import record_delivery_attempt
        from src.generator.delivery import manager_stats
        from src.infra.db import session_scope
        from src.infra.models import CampaignChainToken, CampaignRecipient

        chain = empty_chain()
        root = chain["root_node_id"]
        second_node = "node-second"
        chain["nodes"][0]["name"] = "Письмо 1"
        chain["nodes"].append(
            {
                "id": second_node,
                "name": "Письмо 2",
                "kind": "email",
                "email_template_id": "tmpl-2",
                "document_template_ids": [],
            }
        )
        chain["edges"] = [
            {
                "id": "edge-next",
                "source_id": root,
                "target_id": second_node,
                "button_label": "Продолжить",
            }
        ]
        save_email_chain(self.campaign["id"], self.username, chain)

        event_at = datetime.now(timezone.utc)
        with session_scope() as session:
            recipient = CampaignRecipient(
                campaign_id=self.campaign["id"],
                row_index=0,
                company="ООО Альфа",
                contact_name="Иван",
                email="ivan@example.test",
            )
            session.add(recipient)
            session.flush()
            recipient_id = int(recipient.id)
            session.add(
                CampaignChainToken(
                    token=str(uuid.uuid4()),
                    campaign_id=self.campaign["id"],
                    recipient_id=recipient_id,
                    edge_id="edge-next",
                    source_node_id=root,
                    target_node_id=second_node,
                    clicked_at=event_at,
                    sent_at=event_at,
                    send_status="sent",
                )
            )
        record_delivery_attempt(
            campaign_id=self.campaign["id"],
            recipient_id=recipient_id,
            batch_id="batch-root",
            status="sent",
            delivery_email="ivan@example.test",
            provider_message_id="root-message",
        )

        def delivery_row(
            node_id: str,
            status: str,
            send_mode: str,
            *,
            row_id: str | None = None,
        ) -> dict:
            manager_status = manager_stats.normalize_manager_status(status)
            effective_row_id = row_id or str(recipient_id)
            return {
                "job_id": self.campaign["job_id"],
                "row_id": effective_row_id,
                "email": f"{effective_row_id}@example.test",
                "organization": f"Компания {effective_row_id}",
                "provider": "rusender",
                "manager_status": manager_status,
                "interest": manager_stats.interest_for(manager_status["key"]),
                "sent_at": event_at.isoformat(),
                "sent_at_timestamp": event_at.isoformat(),
                "chain_node_id": node_id,
                "send_mode": send_mode,
            }

        stats = get_chain_click_stats(self.campaign["id"])
        manager_stats._attach_chain_step_analytics(
            self.campaign["job_id"],
            [
                delivery_row(root, "delivered", "chain_root"),
                delivery_row(root, "spam", "chain_root", row_id="spam-root"),
                delivery_row(second_node, "opened", "chain_followup"),
                delivery_row(
                    second_node,
                    "unsubscribed",
                    "chain_followup",
                    row_id="unsubscribed-second",
                ),
            ],
            stats,
        )

        first = stats["steps"][0]["analytics"]["summary"]
        second = stats["steps"][1]["analytics"]["summary"]
        self.assertEqual(
            (
                first["total_attempts"],
                first["sent"],
                first["delivered"],
                first["spam"],
            ),
            (2, 2, 1, 1),
        )
        self.assertEqual(
            (
                second["total_attempts"],
                second["sent"],
                second["opened"],
                second["unsubscribed"],
            ),
            (2, 2, 1, 1),
        )

    def test_normalize_chain_defaults_kind_email(self) -> None:
        chain = empty_chain()
        normalized = normalize_chain(chain)
        self.assertEqual(normalized["version"], 2)
        self.assertEqual(normalized["nodes"][0]["kind"], "email")

    def test_normalize_chain_rejects_duplicate_node_ids(self) -> None:
        chain = empty_chain()
        root = chain["root_node_id"]
        chain["nodes"].append(
            {"id": root, "name": "Дубликат узла", "email_template_id": "t1", "document_template_ids": []},
        )
        with self.assertRaises(ValueError):
            normalize_chain(chain)

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

    def test_delete_standalone_chain_detaches_linked_campaign(self) -> None:
        from src.campaigns.service import update_campaign
        from src.infra.db import session_scope
        from src.infra.models import Campaign

        created = create_chain(self.username, name="Disposable chain")
        chain_id = created["id"]
        update_campaign(
            self.campaign["id"],
            self.username,
            {"email_chain_id": chain_id, "send_scenario": "email_chain"},
        )

        delete_chain(chain_id, self.username)

        with self.assertRaisesRegex(ValueError, "Цепочка не найдена"):
            load_chain(chain_id, self.username)
        with session_scope() as session:
            campaign = session.get(Campaign, self.campaign["id"])
            self.assertIsNotNone(campaign)
            self.assertIsNone(campaign.email_chain_id)
            self.assertEqual(campaign.send_scenario, "consent_then_materials")
            self.assertIsNone(campaign.draft_payload.get("email_chain_id"))
            self.assertEqual(
                campaign.draft_payload.get("send_scenario"), "consent_then_materials"
            )

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
