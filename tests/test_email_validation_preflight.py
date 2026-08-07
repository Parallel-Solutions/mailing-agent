from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select

from src.campaigns.audience_service import create_audience, replace_members
from src.campaigns.email_validation_service import (
    cached_validation_result,
    enqueue_scope_validation,
    record_hard_delivery_failure,
    run_email_validation,
)
from src.campaigns.service import create_campaign, replace_recipients, validate_campaign_for_launch
from src.generator.delivery.email_validation import EmailValidationResult
from src.infra.db import session_scope
from src.infra.models import (
    Audience,
    AudienceMember,
    BackgroundTask,
    CampaignRecipient,
    EmailValidationCache,
    EmailValidationRun,
)
from src.security.user_store import create_user
from src.utils.config import settings
from tests.bootstrap import bootstrap_test_runtime


class EmailValidationPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.username = f"evp{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        self.campaign = create_campaign(self.username, {"name": "SMTP.BZ preflight"})
        self.campaign_id = str(self.campaign["id"])
        self.settings_patches = [
            patch.object(settings, "email_validation_mode", "smtpbz"),
            patch.object(settings, "email_validation_concurrency", 1),
            patch.object(settings, "email_validation_max_attempts", 1),
        ]
        for setting_patch in self.settings_patches:
            setting_patch.start()
            self.addCleanup(setting_patch.stop)

    @staticmethod
    def _result(email: str, status: str) -> EmailValidationResult:
        if status == "valid":
            is_valid = True
            reason_code = "ok_smtpbz"
            reason = ""
        elif status == "invalid":
            is_valid = False
            reason_code = "smtpbz_invalid"
            reason = "Mailbox does not exist."
        else:
            is_valid = False
            reason_code = "smtpbz_unavailable"
            reason = "Validator is temporarily unavailable."
        return EmailValidationResult(
            email=email,
            normalized_email=email.lower(),
            domain=email.rsplit("@", 1)[-1],
            is_valid=is_valid,
            reason_code=reason_code,
            reason=reason,
            checked_at="2026-08-06T12:00:00+00:00",
            details={"mode": "smtpbz"},
        )

    def _replace_test_recipients(self) -> None:
        replace_recipients(
            self.campaign_id,
            self.username,
            [
                {"email": "valid@example.com"},
                {"email": "bad@example.com"},
                {"email": "temp@example.com"},
            ],
        )

    def test_import_is_pending_and_launch_waits_for_preflight(self) -> None:
        self._replace_test_recipients()

        with session_scope() as session:
            recipients = list(
                session.scalars(
                    select(CampaignRecipient)
                    .where(CampaignRecipient.campaign_id == self.campaign_id)
                    .order_by(CampaignRecipient.row_index)
                ).all()
            )
            self.assertEqual([row.validation_status for row in recipients], ["pending"] * 3)
            self.assertTrue(all(not row.excluded for row in recipients))

        validation = validate_campaign_for_launch(self.campaign_id, self.username)
        self.assertFalse(validation["ok"])
        self.assertTrue(any("SMTP.BZ" in str(error) for error in validation["errors"]))

    def test_worker_updates_recipients_and_reuses_persistent_cache(self) -> None:
        self._replace_test_recipients()

        statuses = {
            "valid@example.com": "valid",
            "bad@example.com": "invalid",
            "temp@example.com": "unknown",
        }
        queued = enqueue_scope_validation("campaign", self.campaign_id, self.username)
        with patch(
            "src.campaigns.email_validation_service.validate_configured_email_address",
            side_effect=lambda email, **kwargs: self._result(email, statuses[email]),
        ) as validator:
            completed = run_email_validation({"run_id": queued["id"]})

        self.assertEqual(validator.call_count, 3)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["valid_count"], 1)
        self.assertEqual(completed["invalid_count"], 1)
        self.assertEqual(completed["unknown_count"], 1)

        with session_scope() as session:
            recipients = list(
                session.scalars(
                    select(CampaignRecipient)
                    .where(CampaignRecipient.campaign_id == self.campaign_id)
                    .order_by(CampaignRecipient.row_index)
                ).all()
            )
            self.assertEqual(
                [(row.validation_status, row.excluded) for row in recipients],
                [("valid", False), ("invalid", True), ("unknown", True)],
            )

        launch_validation = validate_campaign_for_launch(self.campaign_id, self.username)
        self.assertFalse(
            any("SMTP.BZ" in str(error) for error in launch_validation["errors"])
        )

        second_campaign = create_campaign(self.username, {"name": "Cached SMTP.BZ preflight"})
        second_campaign_id = str(second_campaign["id"])
        replace_recipients(
            second_campaign_id,
            self.username,
            [
                {"email": "valid@example.com"},
                {"email": "bad@example.com"},
                {"email": "temp@example.com"},
            ],
        )
        second = enqueue_scope_validation("campaign", second_campaign_id, self.username)
        with patch("src.campaigns.email_validation_service.validate_configured_email_address") as validator:
            cached = run_email_validation({"run_id": second["id"]})

        validator.assert_not_called()
        self.assertEqual(cached["status"], "completed")
        self.assertEqual(cached["cached_count"], 3)

        retry = enqueue_scope_validation("campaign", self.campaign_id, self.username, force=True)
        with patch(
            "src.campaigns.email_validation_service.validate_configured_email_address",
            side_effect=lambda email, **kwargs: self._result(email, "valid"),
        ) as validator:
            refreshed = run_email_validation({"run_id": retry["id"], "refresh_unknown": True})

        validator.assert_called_once()
        self.assertEqual(validator.call_args.args[0], "temp@example.com")
        self.assertEqual(refreshed["valid_count"], 2)
        self.assertEqual(refreshed["cached_count"], 2)

        with session_scope() as session:
            retried = list(
                session.scalars(
                    select(CampaignRecipient)
                    .where(CampaignRecipient.campaign_id == self.campaign_id)
                    .order_by(CampaignRecipient.row_index)
                ).all()
            )
            self.assertEqual(
                [(row.validation_status, row.excluded) for row in retried],
                [("valid", False), ("invalid", True), ("valid", False)],
            )

    def test_configuration_error_is_not_retried_for_every_address(self) -> None:
        replace_recipients(
            self.campaign_id,
            self.username,
            [{"email": "quota@example.com"}],
        )
        queued = enqueue_scope_validation("campaign", self.campaign_id, self.username)
        failure = EmailValidationResult(
            email="quota@example.com",
            normalized_email="quota@example.com",
            domain="example.com",
            is_valid=False,
            reason_code="smtpbz_quota_or_request_error",
            reason="Quota exceeded.",
            checked_at="2026-08-07T12:00:00+00:00",
            details={"mode": "smtpbz"},
        )

        with patch.object(settings, "email_validation_max_attempts", 3), patch(
            "src.campaigns.email_validation_service.validate_configured_email_address",
            return_value=failure,
        ) as validator:
            completed = run_email_validation({"run_id": queued["id"]})

        validator.assert_called_once_with("quota@example.com", config=settings)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["unknown_count"], 1)

    def test_force_replaces_a_stuck_running_validation(self) -> None:
        replace_recipients(
            self.campaign_id,
            self.username,
            [{"email": "stuck@example.com"}],
        )
        queued = enqueue_scope_validation("campaign", self.campaign_id, self.username)
        stale_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        with session_scope() as session:
            run = session.get(EmailValidationRun, queued["id"])
            self.assertIsNotNone(run)
            assert run is not None
            run.status = "running"
            run.updated_at = stale_at
            task = session.get(BackgroundTask, queued["task_id"])
            self.assertIsNotNone(task)
            assert task is not None
            task.status = "running"
            task.lease_owner = "test-worker"
            task.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        replacement = enqueue_scope_validation(
            "campaign", self.campaign_id, self.username, force=True
        )

        self.assertNotEqual(replacement["id"], queued["id"])
        with session_scope() as session:
            old_run = session.get(EmailValidationRun, queued["id"])
            old_task = session.get(BackgroundTask, queued["task_id"])
            self.assertIsNotNone(old_run)
            self.assertIsNotNone(old_task)
            assert old_run is not None
            assert old_task is not None
            self.assertEqual(old_run.status, "stale")
            self.assertIsNotNone(old_task.cancel_requested_at)

    def test_audience_preflight_updates_member_statuses_and_quality(self) -> None:
        audience = create_audience(self.username, "Validated audience")
        audience_id = str(audience["id"])
        replace_members(
            audience_id,
            self.username,
            [{"email": "valid@example.com"}, {"email": "bad@example.com"}],
        )
        queued = enqueue_scope_validation("audience", audience_id, self.username)
        with patch(
            "src.campaigns.email_validation_service.validate_configured_email_address",
            side_effect=lambda email, **kwargs: self._result(
                email, "valid" if email == "valid@example.com" else "invalid"
            ),
        ):
            completed = run_email_validation({"run_id": queued["id"]})

        self.assertEqual(completed["status"], "completed")
        with session_scope() as session:
            members = list(
                session.scalars(
                    select(AudienceMember)
                    .where(AudienceMember.audience_id == audience_id)
                    .order_by(AudienceMember.id)
                ).all()
            )
            self.assertEqual(
                sorted((member.validation_status, member.excluded) for member in members),
                [("invalid", True), ("valid", False)],
            )
            stored_audience = session.get(Audience, audience_id)
            self.assertIsNotNone(stored_audience)
            assert stored_audience is not None
            self.assertEqual(stored_audience.quality_score, 50.0)

    def test_hard_bounce_invalidates_cached_address(self) -> None:
        recorded = record_hard_delivery_failure(
            owner_username=self.username,
            email="bounce@example.com",
            provider_status="hard_bounced",
            reason="550 user unknown",
        )

        self.assertTrue(recorded)
        result = cached_validation_result(self.username, "bounce@example.com")
        self.assertFalse(result.is_valid)
        self.assertEqual(result.reason_code, "delivery_hard_bounce")
        with session_scope() as session:
            cache = session.scalar(
                select(EmailValidationCache).where(
                    EmailValidationCache.owner_username == self.username,
                    EmailValidationCache.normalized_email == "bounce@example.com",
                )
            )
            self.assertIsNotNone(cache)
            assert cache is not None
            self.assertEqual(cache.status, "invalid")

    def test_smtp_hard_bounce_text_invalidates_cache_but_soft_bounce_does_not(self) -> None:
        self.assertTrue(
            record_hard_delivery_failure(
                owner_username=self.username,
                email="gone@example.com",
                provider_status="failed",
                reason="550 5.1.1 user unknown",
            )
        )
        self.assertFalse(
            record_hard_delivery_failure(
                owner_username=self.username,
                email="later@example.com",
                provider_status="soft_bounced",
                reason="421 temporarily unavailable",
            )
        )


if __name__ == "__main__":
    unittest.main()
