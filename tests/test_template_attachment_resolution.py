from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.campaigns import template_render_service


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


if __name__ == "__main__":
    unittest.main()
