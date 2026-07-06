from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from src.generator.delivery import email_validation


class SmtpBzEmailValidationTests(unittest.TestCase):
    def test_smtpbz_mode_accepts_valid_response(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(email_validation, "_run_smtpbz_request", return_value='{"valid": true}') as request:
            result = email_validation.validate_email_address(
                "User@Example.com",
                mode="smtp_bz",
                smtpbz_api_key="token",
                timeout_seconds=2,
            )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.normalized_email, "User@example.com")
        self.assertEqual(result.reason_code, "ok_smtpbz")
        sent_request = request.call_args.args[0]
        self.assertEqual(sent_request.get_header("Authorization"), "token")
        self.assertIn("/check/email/User%40example.com", sent_request.full_url)

    def test_smtpbz_mode_rejects_nonexistent_mailbox(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(
            email_validation,
            "_run_smtpbz_request",
            return_value='{"data":{"valid":false},"message":"Email receiver doesn\'t exist"}',
        ):
            result = email_validation.validate_email_address(
                "missing@example.com",
                mode="smtpbz",
                smtpbz_api_key="token",
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_invalid")
        self.assertIn("SMTP.BZ", result.reason)

    def test_smtpbz_mode_fails_open_when_api_is_unavailable(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(email_validation, "_run_smtpbz_request", side_effect=URLError("temporary")):
            result = email_validation.validate_email_address(
                "person@example.com",
                mode="smtpbz",
                smtpbz_api_key="token",
            )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_unavailable")
        self.assertTrue(result.details["smtpbz"]["fail_open"])

    def test_smtpbz_mode_does_not_call_api_without_key(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(email_validation, "_run_smtpbz_request") as request:
            result = email_validation.validate_email_address("person@example.com", mode="smtpbz")

        self.assertTrue(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_not_configured")
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
