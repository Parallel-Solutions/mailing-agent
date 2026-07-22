from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from src.campaigns import document_editor_service, template_service


def _document_template() -> dict:
    return {
        "id": "template-1",
        "name": "Document",
        "template_type": "document",
        "version": {
            "id": "version-1",
            "filename": "document.docx",
            "storage_key": None,
        },
    }


class DocumentEditorServiceTests(unittest.TestCase):
    @patch.object(document_editor_service.secrets, "token_hex", return_value="abcdef1234567890")
    def test_editor_config_accepts_document_and_uses_manual_version_save(self, _token_hex: MagicMock) -> None:
        with (
            patch.object(
                document_editor_service.template_service,
                "get_template",
                return_value=_document_template(),
            ),
            patch.dict(
                os.environ,
                {
                    "ONLYOFFICE_EDITOR_PUBLIC_URL": "http://documents.example.test",
                    "ONLYOFFICE_APP_INTERNAL_URL": "http://app:9806",
                },
                clear=False,
            ),
        ):
            result = document_editor_service.editor_config("template-1", "admin")

        editor = result["config"]["editorConfig"]
        self.assertEqual(result["editor_url"], "http://documents.example.test")
        self.assertEqual(editor["coEditing"], {"mode": "fast", "change": False})
        self.assertTrue(editor["customization"]["autosave"])
        self.assertFalse(editor["customization"]["forcesave"])
        self.assertEqual(result["document_key"], "version1_abcdef1234567890")
        self.assertEqual(result["config"]["document"]["key"], result["document_key"])

    def test_force_save_sends_onlyoffice_command(self) -> None:
        response = MagicMock()
        response.json.return_value = {"error": 0}
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = response

        with (
            patch.object(
                document_editor_service.template_service,
                "get_template",
                return_value=_document_template(),
            ),
            patch.object(document_editor_service.httpx, "Client", return_value=client),
            patch.object(
                document_editor_service.template_service,
                "get_template_version_file",
                return_value={"filename": "document.docx", "content": b"source"},
            ),
            patch.dict(
                os.environ,
                {"ONLYOFFICE_EDITOR_INTERNAL_URL": "http://onlyoffice"},
                clear=False,
            ),
        ):
            result = document_editor_service.force_save(
                "template-1",
                "admin",
                "version-1",
                "version1_abcdef1234567890",
            )

        self.assertEqual(result, {"accepted": True, "key": "version1_abcdef1234567890"})
        client.post.assert_called_once_with(
            "http://onlyoffice/coauthoring/CommandService.ashx",
            json={"c": "forcesave", "key": "version1_abcdef1234567890"},
        )
        response.raise_for_status.assert_called_once_with()

    def test_callback_download_uses_internal_onlyoffice_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ONLYOFFICE_EDITOR_PUBLIC_URL": "http://localhost:8080",
                "ONLYOFFICE_EDITOR_INTERNAL_URL": "http://onlyoffice",
            },
            clear=False,
        ):
            resolved = document_editor_service._internal_download_url(
                "http://localhost:8080/cache/files/data/output.docx?token=1"
            )
        self.assertEqual(resolved, "http://onlyoffice/cache/files/data/output.docx?token=1")

    def test_callback_download_strips_onlyoffice_subpath(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ONLYOFFICE_EDITOR_PUBLIC_URL": "https://offer.parresh.ru/onlyoffice",
                "ONLYOFFICE_EDITOR_INTERNAL_URL": "http://onlyoffice",
            },
            clear=False,
        ):
            resolved = document_editor_service._internal_download_url(
                "https://offer.parresh.ru/onlyoffice/cache/files/data/output.docx?token=1"
            )
        self.assertEqual(resolved, "http://onlyoffice/cache/files/data/output.docx?token=1")

    def test_save_docx_editor_version_accepts_document_type(self) -> None:
        saved = {"id": "template-1", "version": {"id": "version-2"}}
        with (
            patch.object(template_service, "get_template", return_value=_document_template()),
            patch.object(template_service, "Document"),
            patch.object(template_service, "upload_file_version", return_value=saved) as upload,
        ):
            result = template_service.save_docx_editor_version(
                "template-1",
                "admin",
                b"docx-content",
            )

        self.assertEqual(result, saved)
        upload.assert_called_once_with(
            "admin",
            name="Document",
            template_type="document",
            filename="document.docx",
            data=b"docx-content",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            template_id="template-1",
        )


if __name__ == "__main__":
    unittest.main()
