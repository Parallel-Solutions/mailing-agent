from __future__ import annotations



import unittest

from unittest.mock import patch



from src.generator.delivery.smtp_autodiscover import SmtpDiscoveryResult

from src.generator.delivery.smtp_probe import ProbeResult

from src.generator.delivery.smtp_setup_ai import SetupAction

from src.generator.delivery.smtp_setup_orchestrator import analyze_smtp_setup





class SmtpSetupOrchestratorTests(unittest.TestCase):

    @patch("src.generator.delivery.smtp_setup_orchestrator.advise_smtp_setup")

    @patch("src.generator.delivery.smtp_setup_orchestrator.probe_smtp_for_email")

    def test_analyze_gmail_returns_reachable_probe(self, mock_probe, mock_advise) -> None:

        discoveries = [

            SmtpDiscoveryResult(

                provider="gmail",

                host="smtp.gmail.com",

                port=587,

                use_ssl=False,

                use_starttls=True,

                source="preset",

                confidence="high",

            )

        ]

        mock_probe.return_value = (

            ProbeResult(

                host="smtp.gmail.com",

                port=587,

                use_ssl=False,

                use_starttls=True,

                reachable=True,

                provider="gmail",

                source="preset",

                confidence="high",

                banner="220 ready",

            ),

            discoveries,

        )

        mock_advise.return_value = SetupAction(

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

        )



        analysis = analyze_smtp_setup("user@gmail.com")



        self.assertTrue(analysis.probe is not None and analysis.probe.reachable)

        self.assertEqual(analysis.probe.port, 587)

        self.assertEqual(analysis.action.action, "show_app_password")

        self.assertTrue(analysis.setup_session_id)

        self.assertEqual(analysis.discoveries[0]["provider"], "gmail")



    @patch("src.generator.delivery.smtp_setup_orchestrator.build_fallback_setup_action")

    @patch("src.generator.delivery.smtp_setup_orchestrator.advise_smtp_setup")

    @patch("src.generator.delivery.smtp_setup_orchestrator.probe_smtp_for_email")

    def test_reachable_probe_overrides_manual_ai_action(self, mock_probe, mock_advise, mock_fallback) -> None:

        discoveries = [

            SmtpDiscoveryResult(

                provider="gmail",

                host="smtp.gmail.com",

                port=587,

                use_ssl=False,

                use_starttls=True,

                source="preset",

                confidence="high",

            )

        ]

        mock_probe.return_value = (

            ProbeResult(

                host="smtp.gmail.com",

                port=587,

                use_ssl=False,

                use_starttls=True,

                reachable=True,

                provider="gmail",

                source="preset",

                confidence="high",

            ),

            discoveries,

        )

        mock_advise.return_value = SetupAction(

            action="show_manual",

            message_ru="manual",

            instructions=[],

            oauth_provider=None,

            recommended_settings=None,

            ai_used=True,

        )

        mock_fallback.return_value = SetupAction(

            action="show_app_password",

            message_ru="fallback",

            instructions=["step"],

            oauth_provider=None,

            recommended_settings={"host": "smtp.gmail.com", "port": 587, "use_ssl": False, "use_starttls": True, "provider": "gmail"},

        )



        analysis = analyze_smtp_setup("user@gmail.com")



        mock_fallback.assert_called_once()

        self.assertEqual(analysis.action.action, "show_app_password")



    @patch("src.generator.delivery.smtp_setup_orchestrator.advise_smtp_setup")

    @patch("src.generator.delivery.smtp_setup_orchestrator.probe_smtp_for_email")

    def test_analyze_gmail_unreachable_probe_uses_discovery(self, mock_probe, mock_advise) -> None:

        discoveries = [

            SmtpDiscoveryResult(

                provider="gmail",

                host="smtp.gmail.com",

                port=587,

                use_ssl=False,

                use_starttls=True,

                source="preset",

                confidence="high",

            )

        ]

        mock_probe.return_value = (

            ProbeResult(

                host="smtp.gmail.com",

                port=587,

                use_ssl=False,

                use_starttls=True,

                reachable=False,

                provider="gmail",

                source="preset",

                confidence="high",

            ),

            discoveries,

        )

        mock_advise.return_value = SetupAction(

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

        )



        analysis = analyze_smtp_setup("user@gmail.com")



        self.assertFalse(analysis.probe is not None and analysis.probe.reachable)

        self.assertEqual(analysis.probe_status, "skipped")

        self.assertTrue(analysis.discovery_applied)

        self.assertEqual(analysis.action.action, "show_app_password")

        self.assertEqual(analysis.discoveries[0]["provider"], "gmail")



    @patch("src.generator.delivery.smtp_setup_orchestrator.probe_smtp_for_email")

    def test_passes_probe_deadline(self, mock_probe) -> None:

        mock_probe.return_value = (None, [])



        analyze_smtp_setup("user@gmail.com")



        _args, kwargs = mock_probe.call_args

        self.assertIn("deadline", kwargs)

        self.assertGreater(kwargs["deadline"], 0)





if __name__ == "__main__":

    unittest.main()


