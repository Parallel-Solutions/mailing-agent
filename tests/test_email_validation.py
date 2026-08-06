from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.generator.delivery import email_validation


class SmtpBzEmailValidationTests(unittest.TestCase):
    def test_empty_or_unknown_mode_defaults_to_smtpbz(self) -> None:
        self.assertEqual(email_validation.normalize_email_validation_mode(""), "smtpbz")
        self.assertEqual(email_validation.normalize_email_validation_mode("typo"), "smtpbz")

    def test_smtpbz_mode_accepts_valid_response(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(
            email_validation,
            "_run_smtpbz_request",
            return_value='{"result":true,"checks":{"validDeliver":true}}',
        ) as request:
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

    def test_smtpbz_mode_does_not_accept_top_level_result_without_delivery_confirmation(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(email_validation, "_run_smtpbz_request", return_value='{"result": true}'):
            result = email_validation.validate_email_address(
                "User@Example.com",
                mode="smtp_bz",
                smtpbz_api_key="token",
                timeout_seconds=2,
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_unknown")

    def test_smtpbz_mode_rejects_nonexistent_mailbox(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(
            email_validation,
            "_run_smtpbz_request",
            return_value='{"result":false,"message":"Email receiver doesn\'t exist"}',
        ):
            result = email_validation.validate_email_address(
                "missing@example.com",
                mode="smtpbz",
                smtpbz_api_key="token",
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_invalid")
        self.assertIn("SMTP.BZ", result.reason)

    def test_smtpbz_mode_rejects_failed_delivery_check_despite_true_result(self) -> None:
        with patch.object(
            email_validation,
            "_run_smtpbz_request",
            return_value=(
                '{"result":true,"checks":{"validSyntax":true,'
                '"validMxRecord":true,"validDeliver":false},'
                '"smtpMessages":[{"status":521,"message":"521 5.5.1 Protocol error"}]}'
            ),
        ):
            result = email_validation.validate_email_address(
                "vopros@ek-territory.ru",
                mode="smtpbz",
                smtpbz_api_key="token",
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_invalid")
        self.assertIn("доставляемости", result.reason)

    def test_smtpbz_mode_blocks_when_api_is_unavailable(self) -> None:
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

        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_unavailable")
        self.assertFalse(result.details["smtpbz"]["fail_open"])
        self.assertIn("SMTP.BZ", result.reason)

    def test_smtpbz_mode_can_fail_open_when_explicitly_enabled(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(email_validation, "_run_smtpbz_request", side_effect=URLError("temporary")):
            result = email_validation.validate_email_address(
                "person@example.com",
                mode="smtpbz",
                smtpbz_api_key="token",
                smtpbz_fail_open=True,
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

        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_not_configured")
        self.assertIn("API-ключ", result.reason)
        request.assert_not_called()

    def test_smtpbz_mode_can_fail_open_without_key_when_explicitly_enabled(self) -> None:
        with patch.object(
            email_validation,
            "_domain_has_mail_route",
            return_value=(True, "ok_mx", "", {"domain_check": "mx"}),
        ), patch.object(email_validation, "_run_smtpbz_request") as request:
            result = email_validation.validate_email_address(
                "person@example.com",
                mode="smtpbz",
                smtpbz_fail_open=True,
            )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_not_configured")
        self.assertTrue(result.details["smtpbz"]["fail_open"])
        request.assert_not_called()

    def test_smtpbz_mode_blocks_rejected_api_key(self) -> None:
        error = HTTPError("https://api.smtp.bz/v1/check/email/test", 401, "Unauthorized", {}, None)
        error.raw_body = '{"message":"Unauthorized"}'
        with patch.object(email_validation, "_run_smtpbz_request", side_effect=error):
            result = email_validation.validate_email_address(
                "person@example.com",
                mode="smtpbz",
                smtpbz_api_key="bad-token",
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_unauthorized")
        self.assertIn("API-ключ", result.reason)

    def test_smtpbz_mode_blocks_quota_or_request_error(self) -> None:
        error = HTTPError("https://api.smtp.bz/v1/check/email/test", 400, "Bad Request", {}, None)
        error.raw_body = '{"status":"error","message":"Quota exceeded"}'
        with patch.object(email_validation, "_run_smtpbz_request", side_effect=error):
            result = email_validation.validate_email_address(
                "person@example.com",
                mode="smtpbz",
                smtpbz_api_key="token",
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "smtpbz_quota_or_request_error")
        self.assertIn("квоту", result.reason)


if __name__ == "__main__":
    unittest.main()
