from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.campaigns.substitution_engine import (
    find_template_defects,
    html_to_review_text,
    is_blocking_placeholder_defect,
)
from src.campaigns.template_text_review_service import (
    partition_review_messages,
    review_document_text,
    review_document_text_for_placeholders,
    review_rendered_template,
)
from src.campaigns.text_local_review import review_email_text
from tests.bootstrap import bootstrap_test_runtime


class TemplateTextReviewTests(unittest.TestCase):
    def test_is_blocking_placeholder_defect_filters_resolvable_work_title_artifact(self) -> None:
        defects = find_template_defects("на {{вид работ}} для компании", source="rendered")
        artifact = next(item for item in defects if item.kind == "artifact")
        self.assertFalse(is_blocking_placeholder_defect(artifact))

    def test_is_blocking_placeholder_defect_keeps_unresolvable_artifact(self) -> None:
        defects = find_template_defects("на {{ стp }} для компании", source="rendered")
        artifact = next(item for item in defects if item.kind == "artifact")
        self.assertTrue(is_blocking_placeholder_defect(artifact))

    def test_review_rendered_template_skips_resolvable_work_title_artifact(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from sqlalchemy import select

        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign, CampaignRecipient
        from src.security.user_store import create_user

        username = "tplreview3"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Resolvable artifact test", "work_type": "stp_mo"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"]).limit(1)
            )
            assert camp is not None and recipient is not None
            session.expunge(camp)
            session.expunge(recipient)

        issues = review_rendered_template(
            template_id=None,
            template_name="Mail",
            subject_template="",
            body_html_template="<p>на {{вид работ}} для {{company}}</p>",
            body_text_template="",
            recipient=recipient,
            campaign=camp,
            deep=False,
            include_placeholder_issues=True,
            strict_preview=True,
        )
        self.assertFalse(any(issue.get("kind") == "artifact" for issue in issues))

    def test_review_email_text_detects_space_before_period(self) -> None:
        issues = review_email_text("Текст .", field="body")
        self.assertTrue(any(item.kind == "punctuation" and item.severity == "warning" for item in issues))

    def test_review_document_text_includes_language_issues(self) -> None:
        issues = review_document_text(
            "Текст без знака препинания\nПробел перед точкой .",
            template_id="doc-1",
            template_name="Doc",
        )
        self.assertTrue(any(item.get("kind") == "punctuation" for item in issues))
        self.assertTrue(all(item.get("field") == "attachment" for item in issues))

    def test_review_document_text_for_placeholders_keeps_placeholder_only(self) -> None:
        issues = review_document_text_for_placeholders(
            "Текст .",
            template_id="doc-1",
            template_name="Doc",
        )
        self.assertEqual(issues, [])

    def test_review_email_text_suggests_territory_genitive(self) -> None:
        issues = review_email_text(
            "на разработку СТП для территории Энемское городское поселение.",
            field="body",
        )
        case_issues = [item for item in issues if item.kind == "case"]
        self.assertTrue(case_issues)
        self.assertEqual(case_issues[0].severity, "warning")
        self.assertIn("Энемского городского поселения", case_issues[0].suggestion)
        self.assertNotIn("Энемское городское поселение.", case_issues[0].suggestion)

    def test_review_email_text_detects_single_brace_artifact(self) -> None:
        issues = review_email_text(
            "разработку {разработке схемы территориального планирования для территории.",
            field="body",
        )
        self.assertTrue(any(item.kind == "artifact" and item.severity == "error" for item in issues))

    def test_review_email_text_detects_artifact_in_plain_text(self) -> None:
        defects = find_template_defects("на {{ стп }} для", source="rendered")
        self.assertTrue(any(item.kind == "artifact" for item in defects))

    def test_html_to_review_text_strips_tags(self) -> None:
        plain = html_to_review_text("<p>на {{ стп }} для</p>")
        self.assertEqual(plain, "на {{ стп }} для")

    def test_partition_review_messages_splits_severity(self) -> None:
        issues = [
            {"template_name": "A", "message": "error one", "kind": "artifact", "severity": "error"},
            {"template_name": "A", "message": "warn one", "kind": "punctuation", "severity": "warning"},
        ]
        errors, warnings = partition_review_messages(issues)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(warnings), 1)

    def test_issue_dict_downgrades_language_error_to_warning(self) -> None:
        from src.campaigns.template_text_review_service import _issue_dict

        issue = _issue_dict(
            template_id="t1",
            template_name="Mail",
            field="body_html",
            kind="punctuation",
            severity="error",
            fragment=" .",
            message="Пробел перед точкой",
        )
        self.assertEqual(issue["severity"], "warning")

    def test_review_rendered_template_skips_placeholder_issues_by_default(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from sqlalchemy import select

        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign, CampaignRecipient
        from src.security.user_store import create_user

        username = "tplreview1"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Review test"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"]).limit(1)
            )
            assert camp is not None and recipient is not None
            session.expunge(camp)
            session.expunge(recipient)

        issues = review_rendered_template(
            template_id=None,
            template_name="Mail",
            subject_template="",
            body_html_template="<p>на {{ стп }} для {{company}}</p>",
            body_text_template="",
            recipient=recipient,
            campaign=camp,
            deep=False,
        )
        self.assertFalse(any(issue.get("kind") == "artifact" for issue in issues))

    def test_review_rendered_template_includes_placeholder_issues_in_strict_preview(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from sqlalchemy import select

        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign, CampaignRecipient
        from src.security.user_store import create_user

        username = "tplreview2"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Strict preview test"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"]).limit(1)
            )
            assert camp is not None and recipient is not None
            session.expunge(camp)
            session.expunge(recipient)

        issues = review_rendered_template(
            template_id=None,
            template_name="Mail",
            subject_template="",
            body_html_template="<p>на {{ стp }} {{{bad_token}} для {{company}}</p>",
            body_text_template="",
            recipient=recipient,
            campaign=camp,
            deep=False,
            include_placeholder_issues=True,
            strict_preview=True,
        )
        self.assertTrue(any(issue.get("kind") == "artifact" for issue in issues))
        self.assertTrue(any(issue.get("kind") == "malformed" for issue in issues))

    def test_review_document_text_for_placeholders_detects_artifact(self) -> None:
        issues = review_document_text_for_placeholders(
            "на {{ стп }} для компании",
            template_id="doc-1",
            template_name="Attachment",
            field="attachment",
        )
        self.assertTrue(any(issue.get("kind") == "artifact" for issue in issues))
        self.assertTrue(all(issue.get("field") == "attachment" for issue in issues))

    @patch("src.campaigns.template_text_review_service._append_ai_issues")
    @patch("src.campaigns.template_text_review_service._append_case_issues")
    def test_deep_flag_does_not_run_advisory_checks(
        self,
        mock_case: MagicMock,
        mock_ai: MagicMock,
    ) -> None:
        bootstrap_test_runtime(reset_db=True)
        from sqlalchemy import select

        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign, CampaignRecipient
        from src.security.user_store import create_user

        username = "tplreview-deep"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Deep flag test", "work_type": "stp_mo"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"]).limit(1)
            )
            assert camp is not None and recipient is not None
            session.expunge(camp)
            session.expunge(recipient)

        review_rendered_template(
            template_id=None,
            template_name="Mail",
            subject_template="Тема",
            body_html_template="<p>Текст для {{company}}</p>",
            body_text_template="",
            recipient=recipient,
            campaign=camp,
            deep=True,
            advisory=False,
        )
        mock_case.assert_not_called()
        mock_ai.assert_not_called()

    @patch("src.campaigns.template_text_review_service._append_ai_issues")
    @patch("src.campaigns.template_text_review_service._append_case_issues")
    def test_advisory_flag_runs_ai_and_case_checks(
        self,
        mock_case: MagicMock,
        mock_ai: MagicMock,
    ) -> None:
        bootstrap_test_runtime(reset_db=True)
        from sqlalchemy import select

        from src.campaigns.service import create_campaign, replace_recipients
        from src.infra.db import session_scope
        from src.infra.models import Campaign, CampaignRecipient
        from src.security.user_store import create_user

        username = "tplreview-advisory"
        create_user(username, "Pass12345!")
        campaign = create_campaign(username, {"name": "Advisory flag test", "work_type": "stp_mo"})
        replace_recipients(
            campaign["id"],
            username,
            recipients=[{"company": "A", "contact_name": "Ivan", "email": "a@example.com"}],
        )
        with session_scope() as session:
            camp = session.get(Campaign, campaign["id"])
            recipient = session.scalar(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign["id"]).limit(1)
            )
            assert camp is not None and recipient is not None
            session.expunge(camp)
            session.expunge(recipient)

        review_rendered_template(
            template_id=None,
            template_name="Mail",
            subject_template="Тема",
            body_html_template="<p>Текст для {{company}}</p>",
            body_text_template="",
            recipient=recipient,
            campaign=camp,
            advisory=True,
        )
        mock_case.assert_called_once()
        mock_ai.assert_called_once()


if __name__ == "__main__":
    unittest.main()
