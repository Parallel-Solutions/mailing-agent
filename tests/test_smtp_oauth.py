from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from src.generator.delivery.smtp_oauth import (
    OAuthTokens,
    build_xoauth2_string,
    build_oauth_authorize_url,
    encrypt_oauth_tokens,
    decrypt_oauth_tokens,
    oauth_provider_for_email,
    refresh_oauth_tokens,
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

    @patch("src.generator.delivery.smtp_oauth._microsoft_client_id", return_value="client")
    @patch("src.generator.delivery.smtp_oauth._oauth_redirect_uri", return_value="https://example.test/callback")
    def test_microsoft_authorize_requests_smtp_and_imap(
        self,
        _redirect_mock: object,
        _client_mock: object,
    ) -> None:
        url = build_oauth_authorize_url(
            provider="microsoft",
            state="state",
            email="user@example.com",
        )
        scope = parse_qs(urlparse(url).query)["scope"][0]
        self.assertIn("SMTP.Send", scope)
        self.assertIn("IMAP.AccessAsUser.All", scope)

    @patch("src.generator.delivery.smtp_oauth._post_form")
    def test_microsoft_refresh_preserves_old_smtp_only_scope(self, post_mock: object) -> None:
        post_mock.return_value = {
            "access_token": "new-access",
            "expires_in": 3600,
        }
        old_scope = (
            "https://outlook.office.com/SMTP.Send "
            "offline_access openid profile email"
        )
        tokens = refresh_oauth_tokens(
            provider="microsoft",
            refresh_token="old-refresh",
            scope=old_scope,
        )
        request_body = post_mock.call_args.args[1]
        self.assertEqual(request_body["scope"], old_scope)
        self.assertEqual(tokens.scope, old_scope)
        self.assertEqual(tokens.refresh_token, "old-refresh")


if __name__ == "__main__":
    unittest.main()
