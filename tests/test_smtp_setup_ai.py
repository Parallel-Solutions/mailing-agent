from __future__ import annotations

import unittest
from unittest.mock import patch

from src.generator.delivery.smtp_setup_ai import advise_smtp_setup, build_fallback_setup_action


class SmtpSetupAiTests(unittest.TestCase):
    def test_gmail_without_oauth_uses_app_password(self) -> None:
        action = build_fallback_setup_action(
            {
                "email": "user@gmail.com",
                "domain": "gmail.com",
                "provider_hint": "gmail",
                "probe": {
                    "host": "smtp.gmail.com",
                    "port": 587,
                    "use_ssl": False,
                    "use_starttls": True,
                    "reachable": True,
                    "provider": "gmail",
                    "source": "probe",
                },
                "oauth_available": {"google": False, "microsoft": False},
            }
        )
        self.assertEqual(action.action, "show_app_password")
        self.assertIsNone(action.oauth_provider)
        self.assertEqual(action.recommended_settings["port"], 587)

    def test_gmail_with_oauth_prefers_oauth(self) -> None:
        action = build_fallback_setup_action(
            {
                "email": "user@gmail.com",
                "domain": "gmail.com",
                "provider_hint": "gmail",
                "probe": {
                    "host": "smtp.gmail.com",
                    "port": 587,
                    "use_ssl": False,
                    "use_starttls": True,
                    "reachable": True,
                    "provider": "gmail",
                    "source": "probe",
                },
                "oauth_available": {"google": True, "microsoft": False},
            }
        )
        self.assertEqual(action.action, "show_oauth")
        self.assertEqual(action.oauth_provider, "google")

    def test_gmail_unreachable_probe_uses_app_password(self) -> None:
        action = build_fallback_setup_action(
            {
                "email": "user@gmail.com",
                "domain": "gmail.com",
                "provider_hint": "gmail",
                "probe": {
                    "host": "smtp.gmail.com",
                    "port": 587,
                    "use_ssl": False,
                    "use_starttls": True,
                    "reachable": False,
                    "provider": "gmail",
                    "source": "preset",
                },
                "discoveries": [
                    {
                        "provider": "gmail",
                        "host": "smtp.gmail.com",
                        "port": 587,
                        "use_ssl": False,
                        "use_starttls": True,
                        "source": "preset",
                        "confidence": "high",
                    }
                ],
                "oauth_available": {"google": False, "microsoft": False},
            }
        )
        self.assertEqual(action.action, "show_app_password")
        self.assertNotEqual(action.action, "show_manual")
        self.assertEqual(action.recommended_settings["host"], "smtp.gmail.com")
        self.assertEqual(action.recommended_settings["port"], 587)

    def test_gmail_unreachable_probe_with_oauth(self) -> None:
        action = build_fallback_setup_action(
            {
                "email": "user@gmail.com",
                "domain": "gmail.com",
                "provider_hint": "gmail",
                "probe": {"reachable": False},
                "discoveries": [
                    {
                        "provider": "gmail",
                        "host": "smtp.gmail.com",
                        "port": 587,
                        "use_ssl": False,
                        "use_starttls": True,
                        "source": "preset",
                        "confidence": "high",
                    }
                ],
                "oauth_available": {"google": True, "microsoft": False},
            }
        )
        self.assertEqual(action.action, "show_oauth")
        self.assertEqual(action.oauth_provider, "google")
        self.assertEqual(action.recommended_settings["host"], "smtp.gmail.com")

    def test_unreachable_probe_suggests_manual(self) -> None:
        action = build_fallback_setup_action(
            {
                "email": "user@corp.example",
                "domain": "corp.example",
                "provider_hint": "custom",
                "probe": {"reachable": False},
                "oauth_available": {"google": False, "microsoft": False},
            }
        )
        self.assertEqual(action.action, "show_manual")

    def test_custom_domain_with_discovery_uses_password(self) -> None:
        action = build_fallback_setup_action(
            {
                "email": "user@corp.example",
                "domain": "corp.example",
                "provider_hint": "custom",
                "probe": {"reachable": False},
                "discoveries": [
                    {
                        "provider": "custom",
                        "host": "smtp.corp.example",
                        "port": 587,
                        "use_ssl": False,
                        "use_starttls": True,
                        "source": "thunderbird",
                        "confidence": "medium",
                    }
                ],
                "oauth_available": {"google": False, "microsoft": False},
            }
        )
        self.assertEqual(action.action, "show_password")
        self.assertEqual(action.recommended_settings["host"], "smtp.corp.example")

    def test_auth_error_suggests_app_password(self) -> None:
        action = build_fallback_setup_action(
            {
                "email": "user@yandex.ru",
                "domain": "yandex.ru",
                "provider_hint": "yandex",
                "last_error": "535 authentication failed",
                "probe": {
                    "host": "smtp.yandex.ru",
                    "port": 587,
                    "use_ssl": False,
                    "use_starttls": True,
                    "reachable": True,
                    "provider": "yandex",
                },
                "oauth_available": {"google": False, "microsoft": False},
            }
        )
        self.assertEqual(action.action, "show_app_password")


    def test_mailru_custom_domain_parresh_uses_app_password(self) -> None:
        action = build_fallback_setup_action(
            {
                "email": "personal.offer@parresh.ru",
                "domain": "parresh.ru",
                "provider_hint": "mailru",
                "probe": {
                    "host": "smtp.mail.ru",
                    "port": 465,
                    "use_ssl": True,
                    "use_starttls": False,
                    "reachable": True,
                    "provider": "mailru",
                    "source": "mx_hint",
                },
                "discoveries": [
                    {
                        "provider": "mailru",
                        "host": "smtp.mail.ru",
                        "port": 465,
                        "use_ssl": True,
                        "use_starttls": False,
                        "source": "mx_hint",
                        "confidence": "high",
                    }
                ],
                "oauth_available": {"google": False, "microsoft": False},
            }
        )
        self.assertEqual(action.action, "show_app_password")
        self.assertEqual(action.recommended_settings["host"], "smtp.mail.ru")

    @patch("src.generator.delivery.smtp_setup_ai._build_llm_client")
    @patch("src.generator.delivery.smtp_setup_ai.SMTP_SETUP_AI_ENABLED", True)
    def test_known_provider_skips_ai_entirely(self, mock_build_client) -> None:
        # Mail.ru is a known provider with a deterministic app-password flow — the AI
        # must not even be consulted, let alone allowed to override it.
        context = {
            "email": "personal.offer@parresh.ru",
            "domain": "parresh.ru",
            "provider_hint": "mailru",
            "probe": {
                "host": "smtp.mail.ru",
                "port": 465,
                "use_ssl": True,
                "use_starttls": False,
                "reachable": True,
                "provider": "mailru",
                "source": "mx_hint",
            },
            "discoveries": [
                {
                    "provider": "mailru",
                    "host": "smtp.mail.ru",
                    "port": 465,
                    "use_ssl": True,
                    "use_starttls": False,
                    "source": "mx_hint",
                    "confidence": "high",
                }
            ],
            "oauth_available": {"google": False, "microsoft": False},
        }

        action = advise_smtp_setup(context)

        mock_build_client.assert_not_called()
        self.assertEqual(action.action, "show_app_password")
        self.assertFalse(action.ai_used)


if __name__ == "__main__":
    unittest.main()
