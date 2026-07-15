from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.generator.delivery.smtp_autodiscover import discover_smtp_candidates, discover_smtp_settings
from src.security.auth import Principal
from src.web.smtp_router import create_smtp_router


class SmtpAutodiscoverTests(unittest.TestCase):
    def test_gmail_domain_uses_gmail_preset(self) -> None:
        result = discover_smtp_settings("user@gmail.com")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.provider, "gmail")
        self.assertEqual(result.host, "smtp.gmail.com")
        self.assertEqual(result.source, "preset")
        self.assertEqual(result.confidence, "high")

    def test_mailru_domain_uses_mailru_preset(self) -> None:
        result = discover_smtp_settings("user@mail.ru")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.provider, "mailru")
        self.assertEqual(result.host, "smtp.mail.ru")
        self.assertEqual(result.confidence, "high")

    @patch("src.generator.delivery.smtp_autodiscover._discover_from_thunderbird")
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_submission_srv")
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_mozilla_autoconfig")
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_microsoft_autodiscover")
    @patch("src.generator.delivery.smtp_autodiscover._lookup_mx_hosts")
    def test_preset_short_circuit_skips_slow_sources(
        self,
        mock_mx: MagicMock,
        mock_ms: MagicMock,
        mock_mozilla: MagicMock,
        mock_srv: MagicMock,
        mock_thunderbird: MagicMock,
    ) -> None:
        results = discover_smtp_candidates("user@gmail.com")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider, "gmail")
        self.assertEqual(results[0].source, "preset")
        mock_mx.assert_not_called()
        mock_ms.assert_not_called()
        mock_mozilla.assert_not_called()
        mock_srv.assert_not_called()
        mock_thunderbird.assert_not_called()

    @patch("src.generator.delivery.smtp_autodiscover._discover_from_thunderbird", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._lookup_mx_hosts", return_value=[])
    def test_unknown_domain_without_hints_returns_none(self, *_mocks: object) -> None:
        result = discover_smtp_settings("user@unknown-example.test")
        self.assertIsNone(result)

    @patch("src.generator.delivery.smtp_autodiscover._discover_from_thunderbird", return_value=None)
    @patch(
        "src.generator.delivery.smtp_autodiscover._lookup_mx_hosts",
        return_value=["emx.mail.ru"],
    )
    def test_mailru_mx_hint_for_custom_domain(self, *_mocks: object) -> None:
        result = discover_smtp_settings("user@parresh.ru")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.provider, "mailru")
        self.assertEqual(result.source, "mx_hint")

    @patch("src.generator.delivery.smtp_autodiscover._discover_from_thunderbird", return_value=None)
    @patch(
        "src.generator.delivery.smtp_autodiscover._lookup_mx_hosts",
        return_value=["aspmx.l.google.com"],
    )
    def test_google_workspace_mx_hint(self, *_mocks: object) -> None:
        result = discover_smtp_settings("user@corp.example")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.provider, "gmail")
        self.assertEqual(result.source, "mx_hint")
        self.assertEqual(result.confidence, "high")

    @patch("src.generator.delivery.smtp_autodiscover._discover_from_thunderbird", return_value=None)
    @patch(
        "src.generator.delivery.smtp_autodiscover._lookup_mx_hosts",
        return_value=["corp.mail.protection.outlook.com"],
    )
    def test_microsoft365_mx_hint(self, *_mocks: object) -> None:
        result = discover_smtp_settings("user@corp.example")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.provider, "outlook")
        self.assertEqual(result.source, "mx_hint")

    @patch("src.generator.delivery.smtp_autodiscover._discover_from_submission_srv", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_mozilla_autoconfig", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_microsoft_autodiscover", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._lookup_mx_hosts", return_value=[])
    @patch("src.generator.delivery.smtp_autodiscover.urlopen")
    def test_thunderbird_known_host_maps_to_provider(self, mock_urlopen: MagicMock, *_mocks: object) -> None:
        xml_payload = b"""<?xml version="1.0"?>
<clientConfig>
  <emailProvider id="corp.example">
    <outgoingServer type="smtp">
      <hostname>smtp.mail.ru</hostname>
      <port>465</port>
      <socketType>SSL</socketType>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""
        response = MagicMock()
        response.read.return_value = xml_payload
        response.__enter__.return_value = response
        mock_urlopen.return_value = response

        result = discover_smtp_settings("user@corp.example")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.provider, "mailru")
        self.assertEqual(result.host, "smtp.mail.ru")
        self.assertEqual(result.source, "thunderbird")

    @patch("src.generator.delivery.smtp_autodiscover._discover_from_submission_srv", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_mozilla_autoconfig", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_microsoft_autodiscover", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._lookup_mx_hosts", return_value=[])
    @patch("src.generator.delivery.smtp_autodiscover.urlopen")
    def test_thunderbird_autoconfig_parses_outgoing_server(self, mock_urlopen: MagicMock, *_mocks: object) -> None:
        xml_payload = b"""<?xml version="1.0"?>
<clientConfig>
  <emailProvider id="example.test">
    <outgoingServer type="smtp">
      <hostname>smtp.example.test</hostname>
      <port>587</port>
      <socketType>STARTTLS</socketType>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""
        response = MagicMock()
        response.read.return_value = xml_payload
        response.__enter__.return_value = response
        mock_urlopen.return_value = response

        result = discover_smtp_settings("user@example.test")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.provider, "custom")
        self.assertEqual(result.host, "smtp.example.test")
        self.assertEqual(result.port, 587)
        self.assertFalse(result.use_ssl)
        self.assertTrue(result.use_starttls)
        self.assertEqual(result.source, "thunderbird")
        self.assertEqual(result.confidence, "medium")

    @patch("src.generator.delivery.smtp_autodiscover._discover_from_submission_srv", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_mozilla_autoconfig", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._discover_from_microsoft_autodiscover", return_value=None)
    @patch("src.generator.delivery.smtp_autodiscover._lookup_mx_hosts", return_value=[])
    @patch("src.generator.delivery.smtp_autodiscover.urlopen")
    def test_thunderbird_ssl_socket_type(self, mock_urlopen: MagicMock, *_mocks: object) -> None:
        xml_payload = b"""<?xml version="1.0"?>
<clientConfig>
  <emailProvider id="example.test">
    <outgoingServer type="smtp">
      <hostname>smtp.example.test</hostname>
      <port>465</port>
      <socketType>SSL</socketType>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""
        response = MagicMock()
        response.read.return_value = xml_payload
        response.__enter__.return_value = response
        mock_urlopen.return_value = response

        result = discover_smtp_settings("user@example.test")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.use_ssl)
        self.assertFalse(result.use_starttls)

    def test_invalid_email_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            discover_smtp_settings("not-an-email")


class SmtpDiscoverApiTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(
            create_smtp_router(
                check_auth=lambda: Principal(username="discover-user", tenant_id="tenant-a", role="user"),
            )
        )
        return TestClient(app)

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


if __name__ == "__main__":
    unittest.main()
