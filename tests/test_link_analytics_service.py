from __future__ import annotations

import unittest
import unittest.mock
import uuid

from src.campaigns import link_analytics_service
from tests.bootstrap import bootstrap_test_runtime


class LinkAnalyticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.security.user_store import create_user

        self.username = f"links{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")

    def _campaign_with_template(self, body_html: str):
        from src.campaigns.service import create_campaign, update_campaign
        from src.campaigns.template_service import create_template

        template = create_template(
            self.username,
            name="Первое письмо",
            template_type="email",
            subject="Тема",
            body_html=body_html,
        )
        campaign = create_campaign(
            self.username,
            {
                "name": "Рассылка со ссылками",
                "send_scenario": "single_email",
            },
        )
        return update_campaign(
            campaign["id"],
            self.username,
            {"email_template_id": template["id"], "send_scenario": "single_email"},
        )

    def test_standalone_email_without_links_hides_click_analytics(self) -> None:
        campaign = self._campaign_with_template("<p>Обычный текст без ссылки</p>")

        with unittest.mock.patch.object(
            link_analytics_service,
            "_provider_click_events",
            return_value=[],
        ):
            result = link_analytics_service.build_campaign_link_analytics(
                campaign["job_id"],
                campaign,
            )

        self.assertFalse(result["has_links"])
        self.assertEqual(result["steps"], [])

    def test_standalone_link_contains_unique_clickers(self) -> None:
        from src.infra.db import session_scope
        from src.infra.models import CampaignRecipient

        campaign = self._campaign_with_template(
            '<p><a href="https://example.test/offer">Получить предложение</a></p>'
        )
        with session_scope() as session:
            session.add(
                CampaignRecipient(
                    campaign_id=campaign["id"],
                    row_index=0,
                    company="ООО Альфа",
                    contact_name="Иван",
                    email="ivan@example.test",
                )
            )

        provider_events = [
            {
                "url": "https://example.test/offer",
                "email": "ivan@example.test",
                "row_id": "0",
                "clicked_at": "2026-07-29T10:00:00+00:00",
                "provider": "rusender",
            },
            {
                "url": "https://example.test/offer",
                "email": "ivan@example.test",
                "row_id": "0",
                "clicked_at": "2026-07-29T10:01:00+00:00",
                "provider": "rusender",
            },
        ]
        with unittest.mock.patch.object(
            link_analytics_service,
            "_provider_click_events",
            return_value=provider_events,
        ):
            result = link_analytics_service.build_campaign_link_analytics(
                campaign["job_id"],
                campaign,
            )

        self.assertTrue(result["has_links"])
        self.assertEqual(result["total_clicks"], 2)
        self.assertEqual(result["unique_clickers"], 1)
        link = result["steps"][0]["links"][0]
        self.assertEqual(link["label"], "Получить предложение")
        self.assertEqual(link["unique_clickers"], 1)
        self.assertEqual(link["clickers"][0]["company"], "ООО Альфа")

    def test_chain_includes_links_declared_inside_email_template(self) -> None:
        from src.campaigns.chain_service import empty_chain, save_email_chain
        from src.campaigns.service import create_campaign, update_campaign
        from src.campaigns.template_service import create_template

        template = create_template(
            self.username,
            name="Письмо со внутренней ссылкой",
            template_type="email",
            subject="Тема",
            body_html='<p><a href="https://inside.example.test/page">Внутренняя ссылка</a></p>',
        )
        campaign = create_campaign(
            self.username,
            {"name": "Цепочка со ссылкой", "send_scenario": "email_chain"},
        )
        chain = empty_chain()
        chain["nodes"][0]["email_template_id"] = template["id"]
        save_email_chain(campaign["id"], self.username, chain)
        campaign = update_campaign(
            campaign["id"],
            self.username,
            {"send_scenario": "email_chain"},
        )

        result = link_analytics_service.build_campaign_link_analytics(
            campaign["job_id"],
            campaign,
        )

        self.assertTrue(result["has_links"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(
            result["steps"][0]["links"][0]["label"],
            "Внутренняя ссылка",
        )
        self.assertEqual(result["steps"][0]["links"][0]["unique_clickers"], 0)

    def test_template_links_are_deduplicated_and_ignore_mailto(self) -> None:
        links = link_analytics_service._template_links(
            (
                '<a href="https://example.test/path/">Сайт</a>'
                '<a href="https://example.test/path">Дубликат</a>'
                '<a href="mailto:hello@example.test">Почта</a>'
            ),
            "Подробнее: https://example.test/path",
        )

        self.assertEqual(links, [{"url": "https://example.test/path", "label": "Сайт"}])


if __name__ == "__main__":
    unittest.main()
