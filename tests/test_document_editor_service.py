from __future__ import annotations

import base64
import hashlib
import hmac
import json
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
                    "ONLYOFFICE_JWT_SECRET": "test-onlyoffice-jwt-secret",
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
        token = result["config"]["token"]
        encoded_header, encoded_payload, encoded_signature = token.split(".")
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected_signature = hmac.new(
            b"test-onlyoffice-jwt-secret",
            signing_input,
            hashlib.sha256,
        ).digest()
        actual_signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        self.assertEqual(actual_signature, expected_signature)
        token_payload = json.loads(
            base64.urlsafe_b64decode(
                encoded_payload + "=" * (-len(encoded_payload) % 4)
            ).decode("utf-8")
        )
        unsigned_config = dict(result["config"])
        unsigned_config.pop("token")
        self.assertEqual(token_payload, unsigned_config)

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
                {
                    "ONLYOFFICE_EDITOR_INTERNAL_URL": "http://onlyoffice",
                    "ONLYOFFICE_JWT_SECRET": "test-onlyoffice-jwt-secret",
                },
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
        call = client.post.call_args
        self.assertEqual(
            call.args[0],
            "http://onlyoffice/command?shardkey=version1_abcdef1234567890",
        )
        self.assertEqual(call.kwargs["json"]["c"], "forcesave")
        self.assertEqual(call.kwargs["json"]["key"], "version1_abcdef1234567890")
        self.assertEqual(
            call.kwargs["headers"]["Authorization"],
            f"Bearer {call.kwargs['json']['token']}",
        )
        response.raise_for_status.assert_called_once_with()

    def test_force_save_without_jwt_keeps_local_development_compatible(self) -> None:
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
                {
                    "ONLYOFFICE_EDITOR_INTERNAL_URL": "http://onlyoffice",
                    "ONLYOFFICE_JWT_SECRET": "",
                },
                clear=False,
            ),
        ):
            document_editor_service.force_save(
                "template-1",
                "admin",
                "version-1",
                "version1_abcdef1234567890",
            )

        client.post.assert_called_once_with(
            "http://onlyoffice/command?shardkey=version1_abcdef1234567890",
            json={"c": "forcesave", "key": "version1_abcdef1234567890"},
            headers={},
        )

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
