from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet

from src.campaigns.connection_service import create_connection, list_connections
from src.security.user_store import create_user
from tests.bootstrap import bootstrap_test_runtime


class ConnectionOAuthCreateTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.owner = f"owner-{uuid.uuid4().hex[:8]}"
        create_user(self.owner, "Pass12345!")

    def test_create_oauth_connection_stores_tokens_and_reports_secret(self) -> None:
        created = create_connection(
            self.owner,
            {
                "transport": "smtp",
                "provider": "gmail",
                "email": "user@gmail.com",
                "auth_method": "oauth",
                "oauth_provider": "google",
                "oauth_tokens": {
                    "access_token": "access-token-1",
                    "refresh_token": "refresh-token-1",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "https://mail.google.com/",
                },
                "host": "smtp.gmail.com",
                "port": 587,
                "use_ssl": False,
                "use_starttls": True,
            },
        )
        self.assertEqual(created["transport"], "smtp")
        self.assertEqual(created["provider"], "gmail")
        self.assertEqual(created["auth_method"], "oauth")
        self.assertEqual(created["oauth_provider"], "google")
        self.assertTrue(created["has_secret"])
        self.assertNotIn("oauth_tokens", created)
        self.assertNotIn("password_encrypted", created)

        listed = list_connections(self.owner)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["auth_method"], "oauth")
        self.assertTrue(listed[0]["has_secret"])

    def test_create_oauth_requires_tokens(self) -> None:
        with self.assertRaises(ValueError):
            create_connection(
                self.owner,
                {
                    "transport": "smtp",
                    "provider": "gmail",
                    "email": "user@gmail.com",
                    "auth_method": "oauth",
                    "oauth_provider": "google",
                },
            )


if __name__ == "__main__":
    unittest.main()
