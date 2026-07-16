from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet

from src.campaigns.connection_service import create_connection, resolve_connection
from src.campaigns.profile_service import update_profile
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class ConnectionSenderNameFromProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.owner = f"owner-{uuid.uuid4().hex[:8]}"
        create_user(self.owner, "Pass12345!")
        update_profile(self.owner, {"display_name": "Профиль Отправитель"})

    def test_create_without_sender_name_uses_profile_display_name(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "rusender",
                "email": "verified@example.com",
                "api_token": "rs_ck_secret",
            },
        )
        self.assertEqual(created["sender_name"], "Профиль Отправитель")

    def test_resolve_prefers_current_profile_display_name(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "mailopost",
                "email": "verified@example.com",
                "api_token": "mp_token",
                "sender_name": "Старое имя",
            },
        )
        update_profile(self.owner, {"display_name": "Новое имя из профиля"})
        resolved = resolve_connection(created["id"], self.owner)
        self.assertEqual(resolved.sender_name, "Новое имя из профиля")

    def test_resolve_falls_back_to_connection_sender_name(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "rusender",
                "email": "verified@example.com",
                "api_token": "rs_ck_secret",
                "sender_name": "Из подключения",
            },
        )
        update_profile(self.owner, {"display_name": ""})
        resolved = resolve_connection(created["id"], self.owner)
        self.assertEqual(resolved.sender_name, "Из подключения")


if __name__ == "__main__":
    unittest.main()
