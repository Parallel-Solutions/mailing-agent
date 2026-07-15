from __future__ import annotations

import unittest

from src.generator.delivery.smtp_oauth import (
    OAuthTokens,
    build_xoauth2_string,
    encrypt_oauth_tokens,
    decrypt_oauth_tokens,
    oauth_provider_for_email,
)
from src.security.credential_vault import decrypt_secret, encrypt_secret
from unittest.mock import patch
from cryptography.fernet import Fernet


class SmtpOAuthTests(unittest.TestCase):
    def test_oauth_provider_for_gmail(self) -> None:
        self.assertEqual(oauth_provider_for_email("user@gmail.com"), "google")

    def test_oauth_provider_for_outlook(self) -> None:
        self.assertEqual(oauth_provider_for_email("user@outlook.com"), "microsoft")

    def test_build_xoauth2_string(self) -> None:
        encoded = build_xoauth2_string("user@gmail.com", "token-123")
        self.assertTrue(encoded)
        self.assertNotIn(" ", encoded)

    def test_encrypt_decrypt_oauth_tokens(self) -> None:
        from src.utils.config import settings

        key = Fernet.generate_key().decode("ascii")
        with patch.object(settings, "smtp_credentials_key", key):
            token = OAuthTokens(access_token="access", refresh_token="refresh")
            encrypted = encrypt_oauth_tokens(token)
            restored = decrypt_oauth_tokens(encrypted)
        self.assertEqual(restored.access_token, "access")
        self.assertEqual(restored.refresh_token, "refresh")


if __name__ == "__main__":
    unittest.main()
