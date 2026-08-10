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
                check_auth=lambda: Principal(username=self.owner, tenant_id="tenant-a", role="user"),
            )
        )
        return TestClient(app)

    def test_api_does_not_return_password(self) -> None:
        client = self._client()
        with patch("src.web.smtp_router.verify_and_mark_mailbox", return_value=None):
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

    def test_discover_gmail(self) -> None:
        client = self._client()
        response = client.get("/api/smtp/discover", params={"email": "test@gmail.com"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertTrue(result["discovered"])
        self.assertEqual(result["provider"], "gmail")
        self.assertEqual(result["domain"], "gmail.com")
        self.assertEqual(result["confidence"], "high")

    def test_discover_invalid_email(self) -> None:
        client = self._client()
        empty_response = client.get("/api/smtp/discover", params={"email": ""})
        self.assertEqual(empty_response.status_code, 400)

        invalid_response = client.get("/api/smtp/discover", params={"email": "not-an-email"})
        self.assertEqual(invalid_response.status_code, 400)

    @patch("src.web.smtp_router.discover_smtp_settings", return_value=None)
    def test_discover_unknown_domain(self, _mock_discover: object) -> None:
        client = self._client()
        response = client.get("/api/smtp/discover", params={"email": "user@unknown-example.test"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertFalse(result["discovered"])
        self.assertEqual(result["email"], "user@unknown-example.test")
        self.assertEqual(result["domain"], "unknown-example.test")

    @patch("src.web.smtp_router.analyze_smtp_setup")
    def test_setup_analyze_endpoint(self, mock_analyze: object) -> None:
        from src.generator.delivery.smtp_setup_ai import SetupAction
        from src.generator.delivery.smtp_setup_orchestrator import SetupAnalysis

        mock_analyze.return_value = SetupAnalysis(
            setup_session_id="session-1",
            email="user@gmail.com",
            domain="gmail.com",
            probe=None,
            discoveries=[],
            action=SetupAction(
                action="show_app_password",
                message_ru="Gmail готов",
                instructions=["Создайте пароль приложения"],
                oauth_provider=None,
                recommended_settings={
                    "provider": "gmail",
                    "host": "smtp.gmail.com",
                    "port": 587,
                    "use_ssl": False,
                    "use_starttls": True,
                },
            ),
        )
        client = self._client()
        response = client.post("/api/smtp/setup/analyze", json={"email": "user@gmail.com"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertEqual(result["setup_session_id"], "session-1")
        self.assertEqual(result["action"]["action"], "show_app_password")

    def test_api_test_connection_with_mailbox_id(self) -> None:
        client = self._client()
        create_response = client.post(
            "/api/smtp/mailboxes",
            json={
                "provider": "gmail",
                "email": "api@example.com",
                "password": "api-secret",
                "make_default": True,
                "send_test": False,
            },
        )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        mailbox_id = create_response.json()["result"]["mailbox"]["id"]

        with patch("src.web.smtp_router.verify_and_mark_mailbox", return_value=None):
            response = client.post(
                "/api/smtp/test",
                json={
                    "mailbox_id": mailbox_id,
                    "email": "api@example.com",
                    "password": "new-password",
                    "provider": "gmail",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("principal", response.text.lower())


class SenderAgentSmtpMailboxTests(unittest.TestCase):
    def test_send_via_smtp_uses_mailbox_credentials(self) -> None:
        from email.message import EmailMessage
        from unittest.mock import MagicMock

        from src.generator.delivery import sender_agent
        from src.generator.delivery.smtp_mailboxes import ResolvedSmtpCredentials
        from src.generator.delivery.imap_sent import SentCopyResult

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
        ), patch.object(sender_agent, "_build_message", return_value=message), patch(
            "src.generator.delivery.imap_sent.archive_sent_copy",
            return_value=SentCopyResult(status="archived", folder="Sent"),
        ) as archive_mock, patch(
            "src.generator.delivery.smtp_mailboxes._open_smtp_connection",
            return_value=mock_server,
        ):
            result = sender_agent._send_via_smtp(
                {"MUN_NAME": "Тест"},
                "recipient@example.com",
                [],
                "Тема",
                smtp_mailbox_id="mailbox-1",
                owner_username="owner",
            )
        self.assertIsNone(result)
        mock_server.sendmail.assert_called_once()
        envelope_from, recipients, raw_message = mock_server.sendmail.call_args.args
        self.assertEqual(envelope_from, "mailbox@example.com")
        self.assertEqual(recipients, ["recipient@example.com"])
        self.assertIn(b"Message-ID:", raw_message)
        archive_mock.assert_called_once()
        self.assertEqual(
            archive_mock.call_args.kwargs["raw_message"], raw_message
        )


class SmtpTestEmailTests(unittest.TestCase):
    @patch("src.generator.delivery.smtp_mailboxes._open_smtp_connection")
    def test_test_email_does_not_call_smtpbz(self, open_connection: object) -> None:
        from unittest.mock import MagicMock

        from src.generator.delivery.smtp_mailboxes import (
            ResolvedSmtpCredentials,
            send_test_email,
        )

        server = MagicMock()
        open_connection.return_value = server  # type: ignore[attr-defined]
        credentials = ResolvedSmtpCredentials(
            email="sender@example.com",
            password="secret",
            host="smtp.example.com",
            port=587,
            use_ssl=False,
            use_starttls=True,
        )

        with patch("src.generator.delivery.email_validation._run_smtpbz_request") as smtpbz:
            send_test_email(credentials, recipient="recipient@example.com")

        smtpbz.assert_not_called()
        server.sendmail.assert_called_once()


class NormalizeSmtpSecretTests(unittest.TestCase):
    def test_strips_all_whitespace(self) -> None:
        from src.generator.delivery.smtp_mailboxes import normalize_smtp_secret

        self.assertEqual(normalize_smtp_secret("abcd efgh ijkl mnop"), "abcdefghijklmnop")
        self.assertEqual(normalize_smtp_secret("  ab cd\tef  "), "abcdef")
        self.assertEqual(normalize_smtp_secret(""), "")
        self.assertEqual(normalize_smtp_secret(None), "")


class HumanizeSmtpErrorTests(unittest.TestCase):
    def test_network_unreachable_oserror(self) -> None:
        from src.generator.delivery.smtp_mailboxes import humanize_smtp_error

        message = humanize_smtp_error(OSError(101, "Network is unreachable"))
        self.assertIn("Нет доступа к SMTP-серверу", message)
        self.assertIn("465", message)
        self.assertIn("хоста", message)

    def test_timeout_error(self) -> None:
        from src.generator.delivery.smtp_mailboxes import humanize_smtp_error

        message = humanize_smtp_error(TimeoutError("timed out"))
        self.assertIn("не ответил вовремя", message)
        self.assertIn("провайдер", message)

    def test_auth_error_mentions_gmail_app_password(self) -> None:
        import smtplib

        from src.generator.delivery.smtp_mailboxes import humanize_smtp_error

        message = humanize_smtp_error(
            smtplib.SMTPAuthenticationError(535, b"auth failed"),
            provider="gmail",
            host="smtp.gmail.com",
        )
        self.assertIn("пароль приложения", message)
        self.assertIn("apppasswords", message)

    def test_auth_error_mentions_mailru_external_password(self) -> None:
        import smtplib

        from src.generator.delivery.smtp_mailboxes import humanize_smtp_error

        message = humanize_smtp_error(
            smtplib.SMTPAuthenticationError(535, b"auth failed"),
            provider="mailru",
            host="smtp.mail.ru",
            email="personal.offer@parresh.ru",
        )
        self.assertIn("Почты Mail", message)
        self.assertIn("внешнего приложения", message)
        self.assertNotIn("Gmail", message)
        self.assertNotIn("apppasswords", message)

    def test_auth_error_without_provider_is_generic(self) -> None:
        import smtplib

        from src.generator.delivery.smtp_mailboxes import humanize_smtp_error

        message = humanize_smtp_error(smtplib.SMTPAuthenticationError(535, b"auth failed"))
        self.assertIn("пароль приложения", message)
        self.assertNotIn("Gmail", message)
        self.assertNotIn("Почты Mail", message)


if __name__ == "__main__":
    unittest.main()
