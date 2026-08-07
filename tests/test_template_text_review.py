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
    review_campaign_templates,
    review_rendered_template,
)
from src.campaigns.text_local_review import review_email_text
from src.generator.philologist.document_review_agent import _run_local_checks
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

    def test_review_document_text_does_not_require_terminal_punctuation(self) -> None:
        issues = review_document_text(
            "Document footer",
            template_id="doc-1",
            template_name="Doc",
        )

        self.assertFalse(any(item.get("kind") == "punctuation" for item in issues))

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

    def test_review_email_text_detects_admin_nominative_after_for(self) -> None:
        issues = review_email_text(
            "Разработка Генплана и ПЗЗ для администрация Дятьковского района.",
            field="subject",
        )

        case_issue = next(item for item in issues if item.kind == "case")
        self.assertEqual(case_issue.fragment, "для администрация")
        self.assertEqual(case_issue.suggestion, "для администрации")

    def test_review_email_text_detects_nested_administration_name(self) -> None:
        issues = review_email_text(
            (
                "Администрации муниципального образования "
                "«Администрация Дятьковского района»."
            ),
            field="body",
        )

        self.assertTrue(
            any(
                item.kind == "grammar"
                and "повтор" in item.message.casefold()
                for item in issues
            )
        )

    def test_docx_review_reuses_campaign_case_and_administration_rules(self) -> None:
        issues = _run_local_checks(
            [
                (
                    "paragraph:1",
                    "Разработка Генплана и ПЗЗ для администрация Дятьковского района.",
                ),
                (
                    "paragraph:2",
                    "Администрации муниципального образования "
                    "«Администрация Любимского муниципального округа Ярославской области».",
                ),
            ]
        )

        self.assertTrue(
            any(
                item.fragment == "для администрация"
                and item.suggestion == "для администрации"
                for item in issues
            )
        )
        self.assertTrue(
            any(
                "повторяется слово" in item.issue.casefold()
                and "Администрация Любимского" not in item.suggestion
                for item in issues
            )
        )

    def test_rendered_review_runs_new_local_rules_without_advisory_mode(self) -> None:
        issues = review_rendered_template(
            template_id=None,
            template_name="Mail",
            subject_template="",
            body_html_template="",
            body_text_template="",
            recipient=MagicMock(),
            campaign=MagicMock(),
            rendered_subject="Разработка для администрация Дятьковского района.",
            rendered_html=(
                "<p>Администрации муниципального образования "
                "«Администрация Дятьковского района».</p>"
            ),
            rendered_text="",
            advisory=False,
        )

        self.assertTrue(any(item.get("kind") == "case" for item in issues))
        self.assertTrue(any(item.get("kind") == "grammar" for item in issues))

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

    def test_html_to_review_text_does_not_add_space_before_punctuation_after_inline_tag(self) -> None:
        plain = html_to_review_text("<p>Hello,<strong> Alice</strong>!</p>")

        self.assertEqual(plain, "Hello, Alice!")
        self.assertFalse(
            any(
                issue.kind == "punctuation" and issue.fragment == " !"
                for issue in review_email_text(plain, field="body")
            )
        )

    def test_html_to_review_text_keeps_separation_between_block_tags(self) -> None:
        self.assertEqual(
            html_to_review_text("<p>First</p><p>Second</p>"),
            "First Second",
        )

    def test_html_to_review_text_keeps_real_space_before_punctuation(self) -> None:
        plain = html_to_review_text("<p>Text <strong>important</strong> !</p>")

        self.assertTrue(
            any(
                issue.kind == "punctuation" and issue.fragment == " !"
                for issue in review_email_text(plain, field="body")
            )
        )

    def test_partition_review_messages_splits_severity(self) -> None:
        issues = [
            {"template_name": "A", "message": "error one", "kind": "artifact", "severity": "error"},
            {"template_name": "A", "message": "warn one", "kind": "punctuation", "severity": "warning"},
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
    def test_deep_flag_runs_advisory_checks(
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
        mock_case.assert_called_once()
        mock_ai.assert_called_once()

    @patch("src.campaigns.variable_match_service._collect_templates_for_validation")
    @patch("src.campaigns.variable_match_service._validation_recipients")
    @patch("src.campaigns.template_text_review_service.render_template_text")
    def test_campaign_review_checks_each_unique_recipient_render(
        self,
        mock_render: MagicMock,
        mock_recipients: MagicMock,
        mock_templates: MagicMock,
    ) -> None:
        first = MagicMock(id="r1", row_index=1, company="Первая")
        second = MagicMock(id="r2", row_index=2, company="Вторая")
        mock_recipients.return_value = [first, second]
        mock_templates.return_value = [
            {
                "template_id": "mail-1",
                "template_name": "Письмо",
                "subject": "для администрация {{company}}.",
                "body_html": "<p>Текст.</p>",
                "body_text": "",
                "text": "",
            }
        ]

        def render(text: str, *, recipient, **_kwargs) -> str:
            return text.replace("{{company}}", recipient.company)

        mock_render.side_effect = render
        issues = review_campaign_templates(MagicMock(), deep=False)

        subject_issues = [item for item in issues if item.get("field") == "subject"]
        self.assertEqual(
            {item.get("recipient_row_index") for item in subject_issues},
            {1, 2},
        )

        with patch(
            "src.campaigns.template_text_review_service._append_case_issues"
        ), patch(
            "src.campaigns.template_text_review_service._append_ai_issues"
        ):
            deep_issues = review_campaign_templates(MagicMock(), deep=True)

        blocking_case_issues = [
            item for item in deep_issues if item.get("kind") == "case"
        ]
        self.assertEqual(
            {item.get("recipient_row_index") for item in blocking_case_issues},
            {1, 2},
        )
        self.assertTrue(all(item.get("severity") == "error" for item in blocking_case_issues))
        self.assertTrue(all(item.get("blocking") is True for item in blocking_case_issues))

    @patch("src.campaigns.variable_match_service._collect_templates_for_validation")
    @patch("src.campaigns.variable_match_service._validation_recipients")
    @patch("src.campaigns.template_text_review_service.render_template_text")
    def test_large_recipient_lists_are_sampled_not_iterated_in_full(
        self,
        mock_render: MagicMock,
        mock_recipients: MagicMock,
        mock_templates: MagicMock,
    ) -> None:
        from src.campaigns.template_text_review_service import _MAX_REVIEW_RECIPIENTS

        total = _MAX_REVIEW_RECIPIENTS + 25
        recipients = [
            MagicMock(id=f"r{i}", row_index=i, company=f"Компания {i}")
            for i in range(1, total + 1)
        ]
        mock_recipients.return_value = recipients
        mock_templates.return_value = [
            {
                "template_id": "mail-1",
                "template_name": "Письмо",
                "subject": "для администрация {{company}}.",
                "body_html": "<p>Текст.</p>",
                "body_text": "",
                "text": "",
            }
        ]

        def render(text: str, *, recipient, **_kwargs) -> str:
            return text.replace("{{company}}", recipient.company)

        mock_render.side_effect = render
        issues = review_campaign_templates(MagicMock(), deep=False)

        checked_rows = {
            item.get("recipient_row_index")
            for item in issues
            if item.get("field") == "subject"
        }
        self.assertEqual(len(checked_rows), _MAX_REVIEW_RECIPIENTS)
        self.assertEqual(checked_rows, set(range(1, _MAX_REVIEW_RECIPIENTS + 1)))

        truncation_notices = [
            item
            for item in issues
            if str(_MAX_REVIEW_RECIPIENTS) in str(item.get("message") or "")
            and str(total) in str(item.get("message") or "")
        ]
        self.assertEqual(len(truncation_notices), 1)
        self.assertEqual(truncation_notices[0].get("severity"), "warning")
        self.assertFalse(truncation_notices[0].get("blocking"))

    @patch("src.campaigns.variable_match_service._collect_templates_for_validation")
    @patch("src.campaigns.variable_match_service._validation_recipients")
    @patch("src.campaigns.template_text_review_service.render_template_text")
    def test_campaign_document_skips_terminal_punctuation_warning(
        self,
        mock_render: MagicMock,
        mock_recipients: MagicMock,
        mock_templates: MagicMock,
    ) -> None:
        mock_recipients.return_value = [MagicMock(id="r1", row_index=1)]
        mock_templates.return_value = [
            {
                "template_id": "doc-1",
                "template_name": "Document",
                "template_kind": "document",
                "subject": "",
                "body_html": "",
                "body_text": "",
                "text": "Document footer",
            }
        ]
        mock_render.side_effect = lambda text, **_kwargs: text

        issues = review_campaign_templates(MagicMock(), deep=False)

        self.assertFalse(any(item.get("kind") == "punctuation" for item in issues))

    @patch("src.campaigns.variable_match_service._collect_templates_for_validation")
    @patch("src.campaigns.variable_match_service._validation_recipients")
    @patch("src.campaigns.template_text_review_service.render_template_text")
    def test_deep_document_review_is_advisory_and_skips_remote_checks(
        self,
        mock_render: MagicMock,
        mock_recipients: MagicMock,
        mock_templates: MagicMock,
    ) -> None:
        mock_recipients.return_value = [MagicMock(id="r1", row_index=1)]
        mock_templates.return_value = [
            {
                "template_id": "pptx-1",
                "template_name": "Presentation",
                "template_kind": "document",
                "subject": "",
                "body_html": "",
                "body_text": "",
                "text": "Разработка для администрация района. Текст {{ стp }}.",
            }
        ]
        mock_render.side_effect = lambda text, **_kwargs: text

        with patch(
            "src.campaigns.template_text_review_service._append_case_issues"
        ) as mock_case, patch(
            "src.campaigns.template_text_review_service._append_ai_issues"
        ) as mock_ai:
            issues = review_campaign_templates(
                MagicMock(),
                deep=True,
                include_placeholder_issues=True,
                strict_preview=True,
            )

        mock_case.assert_not_called()
        mock_ai.assert_not_called()
        case_issue = next(item for item in issues if item.get("kind") == "case")
        self.assertEqual(case_issue["severity"], "warning")
        self.assertFalse(case_issue["blocking"])
        artifact_issue = next(item for item in issues if item.get("kind") == "artifact")
        self.assertEqual(artifact_issue["severity"], "error")
        self.assertTrue(artifact_issue["blocking"])

    def test_ai_review_failure_becomes_non_blocking_warning(self) -> None:
        from src.campaigns.template_text_review_service import _append_ai_issues

        issues: list[dict[str, object]] = []
        with patch(
            "src.generator.generation.config_generator.ENABLE_EMAIL_LANGUAGE_AI",
            True,
        ), patch(
            "src.generator.philologist.document_review_agent._run_ai_review",
            side_effect=RuntimeError("provider timeout"),
        ):
            _append_ai_issues(
                issues,
                template_id="mail-1",
                template_name="Mail",
                blocks=[("subject", "Текст")],
            )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "warning")
        self.assertFalse(issues[0]["blocking"])
        self.assertIn("не мешает отправке", str(issues[0]["message"]))

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

    def test_deep_review_keeps_ai_and_case_agent_feedback_non_blocking(self) -> None:
        from src.campaigns.template_text_review_service import _promote_deep_blocking_issue

        for source in ("ai", "case_agent"):
            issue = {
                "kind": "grammar" if source == "ai" else "case",
                "severity": "warning",
                "suggestion": "corrected",
                "source": source,
                "blocking": False,
            }
            _promote_deep_blocking_issue(issue)
            self.assertEqual(issue["severity"], "warning")
            self.assertFalse(issue["blocking"])

    def test_deep_review_keeps_local_deterministic_errors_blocking(self) -> None:
        from src.campaigns.template_text_review_service import _promote_deep_blocking_issue

        issue = {
            "kind": "case",
            "severity": "warning",
            "suggestion": "corrected",
            "source": "local",
        }
        _promote_deep_blocking_issue(issue)
        self.assertEqual(issue["severity"], "error")
        self.assertTrue(issue["blocking"])

    def test_template_case_fields_only_returns_used_context_fields(self) -> None:
        from src.campaigns.template_text_review_service import _template_case_fields

        self.assertEqual(_template_case_fields("<p>{{ADM_NAME}}</p>{{company}}"), {"ADM_NAME_1"})
        self.assertEqual(_template_case_fields("<p>{{company}}</p>"), set())

    def test_case_value_comparison_normalizes_quotes_and_spacing(self) -> None:
        from src.campaigns.template_text_review_service import _case_values_equivalent

        self.assertTrue(
            _case_values_equivalent(
                'Administration  \u00abExample district\u00bb',
                'administration "Example district"',
            )
        )

    def test_case_agent_reports_only_fields_used_by_template(self) -> None:
        from src.campaigns.template_text_review_service import _append_case_issues

        context = {"ADM_NAME_1": "Administration", "HEAD_FIO_1": "Person"}
        result = {
            "items": [
                {
                    "field": "ADM_NAME_1",
                    "status": "needs_review",
                    "generated_value": "Administration",
                    "corrected_value": "Correct administration",
                    "comment": "Check administration case",
                },
                {
                    "field": "HEAD_FIO_1",
                    "status": "needs_review",
                    "generated_value": "Person",
                    "corrected_value": "Correct person",
                    "comment": "Check name case",
                },
            ]
        }
        issues: list[dict[str, object]] = []
        with patch(
            "src.generator.generation.config_generator.ENABLE_CASE_AGENT", True
        ), patch(
            "src.campaigns.substitution_context.recipient_row", return_value={}
        ), patch(
            "src.generator.generation.transforms.build_document_context", return_value=context
        ), patch(
            "src.generator.inflection.ai_case_agent.run_case_validation_agent", return_value=result
        ):
            _append_case_issues(
                issues,
                template_id="template-1",
                template_name="Template",
                recipient=MagicMock(row_index=1),
                campaign=MagicMock(work_type="test"),
                template_text="{{ADM_NAME}}",
            )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["fragment"], "Administration")
        self.assertEqual(issues[0]["source"], "case_agent")
        self.assertFalse(issues[0]["blocking"])


    def test_case_agent_skips_fix_already_present_in_context(self) -> None:
        from src.campaigns.template_text_review_service import _append_case_issues

        context = {"ADM_NAME_1": "Correct administration"}
        result = {
            "items": [
                {
                    "field": "ADM_NAME_1",
                    "status": "fix",
                    "generated_value": "Old administration",
                    "corrected_value": "Correct administration",
                    "comment": "Administration form changed",
                }
            ]
        }
        issues: list[dict[str, object]] = []
        with patch(
            "src.generator.generation.config_generator.ENABLE_CASE_AGENT", True
        ), patch(
            "src.campaigns.substitution_context.recipient_row", return_value={}
        ), patch(
            "src.generator.generation.transforms.build_document_context", return_value=context
        ), patch(
            "src.generator.inflection.ai_case_agent.run_case_validation_agent", return_value=result
        ):
            _append_case_issues(
                issues,
                template_id="template-1",
                template_name="Template",
                recipient=MagicMock(row_index=1),
                campaign=MagicMock(work_type="test"),
                template_text="{{ADM_NAME_1}}",
            )

        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
