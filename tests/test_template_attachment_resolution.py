from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.campaigns import chain_preview_service, document_layout_service, template_render_service
from src.infra.models import Campaign


class TemplateAttachmentResolutionTests(unittest.TestCase):
    def test_static_original_docx_does_not_analyze_fonts(self) -> None:
        template = SimpleNamespace(
            owner_username="admin",
            attachment_output_format="original",
            is_template=False,
            name="proposal.docx",
        )
        version = SimpleNamespace(
            id="version-1",
            filename="proposal.docx",
            storage_key="template-library/template-1/version-1/source/proposal.docx",
            rendered_pdf_storage_key=None,
            rendered_pdf_filename=None,
        )
        font_service = MagicMock()

        with (
            patch(
                "src.campaigns.template_render_service._load_template",
                return_value=(template, version),
            ),
            patch(
                "src.campaigns.template_render_service.get_bytes",
                return_value=b"PK-static-docx",
            ),
            patch.dict("sys.modules", {"src.campaigns.font_service": font_service}),
        ):
            result = template_render_service.resolve_cached_attachment(
                template_id="template-1",
                recipient_id=1,
                job_id=None,
                owner_username="admin",
            )

        self.assertEqual(result, ("proposal.docx", b"PK-static-docx"))
        font_service.template_font_pack_hash.assert_not_called()

    def test_strict_personalized_resolution_does_not_fall_back_to_source(self) -> None:
        template = SimpleNamespace(
            id="template-1",
            owner_username="admin",
            attachment_output_format="original",
            is_template=True,
            name="proposal.docx",
            template_type="document",
        )
        version = SimpleNamespace(
            id="version-1",
            filename="proposal.docx",
            rendered_pdf_filename=None,
        )
        recipient = SimpleNamespace(
            id=1,
            row_index=1,
            company="Acme",
            contact_name="Alice",
            email="alice@example.com",
            email_fallback="",
            region="",
            extra={},
        )
        campaign = SimpleNamespace(
            id="campaign-1",
            name="Campaign",
            description="",
            work_type="",
            document_mode="",
            draft_payload={},
        )

        with (
            patch(
                "src.campaigns.template_render_service._load_template",
                return_value=(template, version),
            ),
            patch(
                "src.campaigns.template_render_service._cache_path",
                return_value=Path("missing.docx"),
            ),
            patch(
                "src.campaigns.template_render_service.render_document_template_for_recipient",
                side_effect=RuntimeError("render failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "render failed"):
                template_render_service.resolve_cached_attachment(
                    template_id="template-1",
                    recipient_id=1,
                    job_id="job-1",
                    owner_username="admin",
                    campaign=campaign,
                    recipient=recipient,
                    strict=True,
                )

    def test_render_fingerprint_changes_with_recipient_data(self) -> None:
        template = SimpleNamespace(
            id="template-1",
            attachment_output_format="original",
            is_template=True,
            template_type="document",
            enforce_one_page=True,
        )
        version = SimpleNamespace(id="version-1")
        recipient = SimpleNamespace(
            id=1,
            row_index=1,
            company="Acme",
            contact_name="Alice",
            email="alice@example.com",
            email_fallback="",
            region="",
            extra={},
        )
        campaign = SimpleNamespace(
            id="campaign-1",
            name="Campaign",
            description="",
            work_type="",
            document_mode="",
            draft_payload={},
        )

        first = template_render_service._render_cache_fingerprint(
            template=template,
            version=version,
            recipient=recipient,
            campaign=campaign,
        )
        recipient.company = "Other"
        second = template_render_service._render_cache_fingerprint(
            template=template,
            version=version,
            recipient=recipient,
            campaign=campaign,
        )

        self.assertNotEqual(first, second)

    def test_render_fingerprint_changes_with_page_limit(self) -> None:
        template = SimpleNamespace(
            id="template-1",
            attachment_output_format="pdf",
            is_template=True,
            template_type="document",
            enforce_one_page=True,
        )
        version = SimpleNamespace(id="version-1")
        recipient = SimpleNamespace(
            id=1,
            row_index=1,
            company="Acme",
            contact_name="Alice",
            email="alice@example.com",
            email_fallback="",
            region="",
            extra={},
        )
        campaign = SimpleNamespace(
            id="campaign-1",
            name="Campaign",
            description="",
            work_type="",
            document_mode="",
            draft_payload={},
        )

        one_page = template_render_service._render_cache_fingerprint(
            template=template,
            version=version,
            recipient=recipient,
            campaign=campaign,
        )
        template.enforce_one_page = False
        multi_page = template_render_service._render_cache_fingerprint(
            template=template,
            version=version,
            recipient=recipient,
            campaign=campaign,
        )

        self.assertNotEqual(one_page, multi_page)

    def test_non_pdf_cache_requires_matching_render_fingerprint(self) -> None:
        cache_path = MagicMock()
        cache_path.exists.return_value = True
        meta = {
            "renderer_version": template_render_service.DOCUMENT_RENDERER_VERSION,
            "template_version_id": "version-1",
            "render_fingerprint": "fingerprint-1",
        }
        with patch.object(template_render_service, "_read_render_meta", return_value=meta):
            self.assertTrue(
                template_render_service._cached_pdf_is_valid(
                    cache_path,
                    expected_template_version_id="version-1",
                    expected_render_fingerprint="fingerprint-1",
                    require_meta=True,
                )
            )
            self.assertFalse(
                template_render_service._cached_pdf_is_valid(
                    cache_path,
                    expected_template_version_id="version-1",
                    expected_render_fingerprint="fingerprint-2",
                    require_meta=True,
                )
            )

    def test_preview_converts_resolved_personalized_docx_to_pdf(self) -> None:
        campaign = SimpleNamespace(
            id="campaign-1",
            job_id="job-1",
            owner_username="admin",
        )
        recipient = SimpleNamespace(id=1, campaign_id="campaign-1")
        session = MagicMock()
        session.get.side_effect = lambda model, _key: campaign if model is Campaign else recipient

        @contextmanager
        def fake_session_scope():
            yield session

        with (
            patch.object(chain_preview_service, "session_scope", fake_session_scope),
            patch.object(chain_preview_service, "can_access_owner", return_value=True),
            patch.object(
                chain_preview_service,
                "resolve_cached_attachment",
                return_value=("proposal.docx", b"PK-personalized"),
            ) as resolve_mock,
            patch(
                "src.campaigns.template_service._build_document_pdf_artifact",
                return_value=(b"%PDF-preview", "proposal.pdf"),
            ) as convert_mock,
        ):
            result = chain_preview_service.resolve_preview_attachment(
                "campaign-1",
                1,
                "template-1",
                "admin",
                as_pdf=True,
            )

        self.assertEqual(result, ("proposal.pdf", b"%PDF-preview"))
        resolve_mock.assert_called_once_with(
            template_id="template-1",
            recipient_id=1,
            job_id="job-1",
            owner_username="admin",
            campaign=campaign,
            recipient=recipient,
            strict=True,
        )
        convert_mock.assert_called_once_with(
            "proposal.docx",
            b"PK-personalized",
            owner_username="admin",
        )

    def test_layout_review_includes_personalized_docx_preview(self) -> None:
        template = SimpleNamespace(id="template-1", name="Proposal")
        version = SimpleNamespace(
            id="version-1",
            filename="proposal.docx",
            storage_key="templates/proposal.docx",
        )
        campaign = SimpleNamespace(
            id="campaign-1",
            job_id="job-1",
            owner_username="admin",
        )
        recipient = SimpleNamespace(id=1)

        with (
            patch.object(document_layout_service, "get_bytes", return_value=b"PK-source"),
            patch.object(
                document_layout_service,
                "resolve_cached_attachment",
                return_value=("proposal.docx", b"PK-personalized"),
            ) as resolve_mock,
            patch(
                "src.campaigns.template_service._build_document_pdf_artifact",
                return_value=(b"%PDF-preview", "proposal.pdf"),
            ),
            patch.object(document_layout_service, "_preview_data_url", return_value="data:image/png;base64,test"),
        ):
            result = document_layout_service._review_template(
                template=template,
                version=version,
                campaign=campaign,
                recipient=recipient,
            )

        self.assertEqual(result["status"], "preview_only")
        self.assertEqual(result["filename"], "proposal.pdf")
        self.assertEqual(result["before_image"], "data:image/png;base64,test")
        self.assertFalse(result["can_apply"])
        resolve_mock.assert_called_once_with(
            template_id="template-1",
            recipient_id=1,
            job_id="job-1",
            owner_username="admin",
            campaign=campaign,
            recipient=recipient,
            strict=True,
        )


if __name__ == "__main__":
    unittest.main()
