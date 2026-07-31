from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet

from src.campaigns import company_service
from src.campaigns.connection_service import create_connection, pick_available_connection, resolve_connection, resolve_sender_name
from src.campaigns.profile_service import update_profile
from src.infra.models import Campaign
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class ConnectionSenderNameFromCompanyTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.owner = f"owner-{uuid.uuid4().hex[:8]}"
        create_user(self.owner, "Pass12345!")
        update_profile(self.owner, {"display_name": "Профиль Отправитель"})
        self.company = company_service.create_company(name="ООО Отправитель")
        company_service.add_member(self.company["id"], self.owner, role="member")

    def test_create_without_sender_name_uses_company_name(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "rusender",
                "email": "verified@example.com",
                "sending_key_id": 42,
            },
        )
        self.assertEqual(created["sender_name"], "ООО Отправитель")

    def test_resolve_prefers_company_name_over_connection_sender_name(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "mailopost",
                "email": "verified@example.com",
                "api_token": "mp_token",
                "sender_name": "Старое имя",
            },
        )
        resolved = resolve_connection(created["id"], self.owner)
        self.assertEqual(resolved.sender_name, "ООО Отправитель")

    def test_resolve_falls_back_to_connection_sender_name_without_company(self) -> None:
        owner = f"owner-{uuid.uuid4().hex[:8]}"
        create_user(owner, "Pass12345!")
        created = create_connection(
            owner,
            {
                "transport": "rusender",
                "email": "verified@example.com",
                "sending_key_id": 42,
                "sender_name": "Из подключения",
            },
        )
        resolved = resolve_connection(created["id"], owner)
        self.assertEqual(resolved.sender_name, "Из подключения")

    def test_resolve_uses_campaign_company_when_provided(self) -> None:
        other_company = company_service.create_company(name="ООО Другая")
        created = create_connection(
            self.owner,
            {
                "transport": "rusender",
                "email": "verified@example.com",
                "sending_key_id": 42,
                "sender_name": "Из подключения",
            },
        )
        campaign = Campaign(
            id="camp-company-sender",
            owner_username=self.owner,
            name="Campaign",
            work_type="stp_mo",
            draft_payload={"company_id": other_company["id"]},
        )
        resolved = resolve_connection(created["id"], self.owner, campaign=campaign)
        self.assertEqual(resolved.sender_name, "ООО Другая")

    def test_resolve_sender_name_ignores_profile_display_name(self) -> None:
        sender_name = resolve_sender_name(self.owner, fallback="Fallback Sender")
        self.assertEqual(sender_name, "ООО Отправитель")

    def test_pick_available_connection_uses_company_sender_name(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "rusender",
                "email": "verified@example.com",
                "sending_key_id": 42,
                "sender_name": "Из подключения",
            },
        )
        picked = pick_available_connection([created["id"]], self.owner, {}, {})
        self.assertIsNotNone(picked)
        self.assertEqual(picked.sender_name, "ООО Отправитель")


if __name__ == "__main__":
    unittest.main()
