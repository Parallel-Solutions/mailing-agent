from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.generator.delivery.smtp_mailboxes import (
    create_mailbox,
    delete_mailbox,
    list_mailboxes,
    resolve_smtp_credentials,
    set_default_mailbox,
    update_mailbox,
)
from src.security.credential_vault import decrypt_secret, encrypt_secret
from src.web.smtp_router import create_smtp_router
from tests.bootstrap import bootstrap_test_runtime


class CredentialVaultTests(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self) -> None:
        from src.utils.config import settings

        key = Fernet.generate_key().decode("ascii")
        with patch.object(settings, "smtp_credentials_key", key):
            token = encrypt_secret("app-password-123")
            self.assertEqual(decrypt_secret(token), "app-password-123")


class SmtpMailboxStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._settings_patch = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._settings_patch.start()
        self.addCleanup(self._settings_patch.stop)
        self.owner = f"user-{uuid.uuid4().hex[:8]}"

    def test_create_list_update_delete_mailbox(self) -> None:
        created = create_mailbox(
            owner_username=self.owner,
            provider="gmail",
            email="Sender@Example.com",
            password="secret-pass",
            sender_name="Sender Name",
            make_default=True,
        )
        self.assertEqual(created["email"], "sender@example.com")
        self.assertTrue(created["is_default"])
        self.assertNotIn("password", created)
        self.assertNotIn("password_encrypted", created)

        mailboxes = list_mailboxes(self.owner)
        self.assertEqual(len(mailboxes), 1)
        self.assertEqual(mailboxes[0]["id"], created["id"])

        updated = update_mailbox(
            created["id"],
            owner_username=self.owner,
            sender_name="Updated Name",
            password="new-secret",
        )
        self.assertEqual(updated["sender_name"], "Updated Name")

        second = create_mailbox(
            owner_username=self.owner,
            provider="yandex",
            email="second@example.com",
            password="second-pass",
        )
        default = set_default_mailbox(second["id"], owner_username=self.owner)
        self.assertTrue(default["is_default"])

        delete_mailbox(created["id"], owner_username=self.owner)
        remaining = list_mailboxes(self.owner)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], second["id"])
        self.assertTrue(remaining[0]["is_default"])

    def test_resolve_smtp_credentials_uses_mailbox(self) -> None:
        created = create_mailbox(
            owner_username=self.owner,
            provider="custom",
            email="mailbox@example.com",
            password="mailbox-pass",
            host="smtp.example.com",
            port=587,
            use_ssl=False,
            use_starttls=True,
            make_default=True,
        )
        resolved = resolve_smtp_credentials(mailbox_id=created["id"], owner_username=self.owner)
        self.assertEqual(resolved.email, "mailbox@example.com")
        self.assertEqual(resolved.password, "mailbox-pass")
        self.assertEqual(resolved.host, "smtp.example.com")
        self.assertEqual(resolved.port, 587)


class SmtpMailboxApiTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._settings_patch = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._settings_patch.start()
        self.addCleanup(self._settings_patch.stop)
        self.owner = f"api-user-{uuid.uuid4().hex[:8]}"

    def _client(self) -> TestClient:
        from src.security.auth import Principal

        app = FastAPI()
        app.include_router(
            create_smtp_router(
                check_auth=lambda: Principal(username=self.owner, is_admin=False),
            )
        )
        return TestClient(app)

    def test_api_does_not_return_password(self) -> None:
        client = self._client()
        with patch("src.web.smtp_router.send_test_email", return_value=None):
            response = client.post(
                "/api/smtp/mailboxes",
                json={
                    "provider": "gmail",
                    "email": "api@example.com",
                    "password": "api-secret",
                    "make_default": True,
                    "send_test": True,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()["result"]["mailbox"]
        self.assertEqual(payload["email"], "api@example.com")
        self.assertNotIn("password", payload)
        self.assertNotIn("password_encrypted", payload)

        list_response = client.get("/api/smtp/mailboxes")
        self.assertEqual(list_response.status_code, 200)
        mailboxes = list_response.json()["result"]["mailboxes"]
        self.assertEqual(len(mailboxes), 1)
        self.assertNotIn("password", mailboxes[0])


class SenderAgentSmtpMailboxTests(unittest.TestCase):
    def test_send_via_smtp_uses_mailbox_credentials(self) -> None:
        from email.message import EmailMessage
        from unittest.mock import MagicMock

        from src.generator.delivery import sender_agent
        from src.generator.delivery.smtp_mailboxes import ResolvedSmtpCredentials

        credentials = ResolvedSmtpCredentials(
            email="mailbox@example.com",
            password="mailbox-pass",
            host="smtp.example.com",
            port=587,
            use_ssl=False,
            use_starttls=True,
            mailbox_id="mailbox-1",
        )
        mock_server = MagicMock()
        message = EmailMessage()
        with patch.object(sender_agent.settings, "smtp_allow_real_send", True), patch(
            "src.generator.delivery.smtp_mailboxes.resolve_smtp_credentials",
            return_value=credentials,
        ), patch.object(sender_agent, "_build_message", return_value=message), patch.object(
            sender_agent, "_save_sent_copy", return_value="sent-copy.eml"
        ), patch("src.generator.delivery.sender_agent.smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = mock_server
            result = sender_agent._send_via_smtp(
                {"MUN_NAME": "Тест"},
                "recipient@example.com",
                [],
                "Тема",
                smtp_mailbox_id="mailbox-1",
                owner_username="owner",
            )
        self.assertEqual(result, "sent-copy.eml")
        mock_server.login.assert_called_once_with("mailbox@example.com", "mailbox-pass")
        mock_server.send_message.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()
