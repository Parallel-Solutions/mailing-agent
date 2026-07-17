"""ONLYOFFICE integration for editable DOCX template versions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from src.campaigns import template_service

logger = logging.getLogger(__name__)



def _secret() -> bytes:
    value = os.getenv("ONLYOFFICE_EDITOR_TOKEN_SECRET", "campaignflow-local-editor-secret")
    return value.encode("utf-8")


def _encode_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(_secret(), encoded, hashlib.sha256).digest()
    return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def _decode_token(token: str, template_id: str) -> dict[str, Any]:
    try:
        encoded, provided = token.split(".", 1)
        expected = hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
        padded_signature = provided + "=" * (-len(provided) % 4)
        signature = base64.urlsafe_b64decode(padded_signature)
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        padded_payload = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded_payload).decode("utf-8"))
    except Exception as exc:
        raise PermissionError("Недействительная ссылка редактора") from exc
    if str(payload.get("template_id")) != template_id:
        raise PermissionError("Ссылка относится к другому шаблону")
    if int(payload.get("expires") or 0) < int(time.time()):
        raise PermissionError("Срок действия ссылки редактора истёк")
    return payload


def editor_config(template_id: str, owner_username: str) -> dict[str, Any]:
    template = template_service.get_template(template_id, owner_username)
    if not template:
        raise FileNotFoundError("Шаблон не найден")
    if template_service.normalize_file_template_type(str(template.get("template_type") or "")) != "document":
        raise ValueError("В ONLYOFFICE редактируются только исходники DOCX")
    version = template.get("version") or {}
    filename = str(version.get("filename") or "")
    version_id = str(version.get("id") or "")
    if not filename.lower().endswith(".docx") or not version_id:
        raise ValueError("Для документа необходимо загрузить файл DOCX")

    document_key = f"{version_id.replace('-', '')}_{secrets.token_hex(8)}"
    token = _encode_token(
        {
            "template_id": template_id,
            "version_id": version_id,
            "owner": owner_username,
            "expires": int(time.time()) + 24 * 60 * 60,
        }
    )
    app_url = os.getenv("ONLYOFFICE_APP_INTERNAL_URL", "http://app:9806").rstrip("/")
    editor_url = os.getenv("ONLYOFFICE_EDITOR_PUBLIC_URL", "http://localhost:8080").rstrip("/")
    source_url = f"{app_url}/api/v1/templates/{template_id}/office-source?token={token}"
    callback_url = f"{app_url}/api/v1/templates/{template_id}/office-callback?token={token}"
    return {
        "editor_url": editor_url,
        "document_key": document_key,
        "config": {
            "documentType": "word",
            "type": "desktop",
            "document": {
                "fileType": "docx",
                "key": document_key,
                "title": filename,
                "url": source_url,
                "permissions": {
                    "edit": True,
                    "download": False,
                    "print": False,
                    "review": False,
                    "comment": False,
                    "chat": False,
                },
            },
            "editorConfig": {
                "lang": "ru",
                "callbackUrl": callback_url,
                "user": {"id": owner_username, "name": owner_username},
                "coEditing": {
                    "mode": "fast",
                    "change": False,
                },
                "customization": {
                    "autosave": True,
                    "forcesave": False,
                    "compactHeader": True,
                    "compactToolbar": True,
                    "comments": False,
                    "feedback": False,
                    "help": False,
                    "hideRightMenu": True,
                    "hideRulers": True,
                    "integrationMode": "embed",
                    "macros": False,
                    "macrosMode": "disable",
                    "plugins": False,
                    "suggestFeature": False,
                    "toolbarHideFileName": True,
                    "unit": "cm",
                    "uiTheme": "theme-white",
                    "features": {
                        "featuresTips": False,
                        "spellcheck": {"mode": True},
                        "tabBackground": {"mode": "toolbar", "change": False},
                        "tabStyle": {"mode": "line", "change": False},
                    },
                },
            },
            "height": "100%",
            "width": "100%",
        },
    }


def force_save(
    template_id: str,
    owner_username: str,
    version_id: str,
    document_key: str,
) -> dict[str, Any]:
    template = template_service.get_template(template_id, owner_username)
    if not template:
        raise FileNotFoundError("Шаблон не найден")
    if template_service.normalize_file_template_type(str(template.get("template_type") or "")) != "document":
        raise ValueError("В ONLYOFFICE редактируются только исходники DOCX")
    version_file = template_service.get_template_version_file(template_id, version_id, owner_username)
    filename = str(version_file.get("filename") or "")
    if not filename.lower().endswith(".docx"):
        raise ValueError("Для документа необходимо загрузить файл DOCX")

    expected_prefix = f"{version_id.replace('-', '')}_"
    if not document_key.startswith(expected_prefix) or len(document_key) > 128:
        raise ValueError("Invalid editor session key")
    editor_internal_url = os.getenv("ONLYOFFICE_EDITOR_INTERNAL_URL", "http://onlyoffice").rstrip("/")
    endpoint = f"{editor_internal_url}/coauthoring/CommandService.ashx"
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10)) as client:
            for attempt in range(5):
                response = client.post(endpoint, json={"c": "forcesave", "key": document_key})
                response.raise_for_status()
                payload = response.json()
                if int(payload.get("error") or 0) != 4 or attempt == 4:
                    break
                time.sleep(0.4)
    except Exception as exc:
        raise RuntimeError("ONLYOFFICE не принял команду сохранения") from exc
    error = int(payload.get("error") or 0)
    if error != 0:
        raise RuntimeError(f"ONLYOFFICE не смог сохранить документ (код {error})")
    return {"accepted": True, "key": document_key}


def source_file(template_id: str, token: str) -> dict[str, Any]:
    payload = _decode_token(token, template_id)
    return template_service.get_template_version_file(
        template_id,
        str(payload["version_id"]),
        str(payload["owner"]),
    )


def _internal_download_url(download_url: str) -> str:
    parsed = urlsplit(download_url)
    public_editor = urlsplit(os.getenv("ONLYOFFICE_EDITOR_PUBLIC_URL", "http://localhost:8080"))
    internal_editor = urlsplit(os.getenv("ONLYOFFICE_EDITOR_INTERNAL_URL", "http://onlyoffice"))
    local_hosts = {"localhost", "127.0.0.1", "0.0.0.0"}
    if parsed.hostname in local_hosts or (
        public_editor.hostname and parsed.hostname == public_editor.hostname
    ):
        return urlunsplit(
            (
                internal_editor.scheme or "http",
                internal_editor.netloc,
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
    return download_url




def handle_callback(template_id: str, token: str, payload: dict[str, Any]) -> dict[str, int]:
    claims = _decode_token(token, template_id)
    status = int(payload.get("status") or 0)
    # Status 2 is the automatic final save after the editor is closed. The
    # product uses explicit version saving, so only a user-initiated forcesave
    # (status 6) is persisted to our archive.
    if status != 6:
        return {"error": 0}
    download_url = str(payload.get("url") or "")
    if not download_url:
        return {"error": 1}
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=10)) as client:
            response = client.get(_internal_download_url(download_url))
            response.raise_for_status()
        template_service.save_docx_editor_version(
            template_id,
            str(claims["owner"]),
            response.content,
        )
    except Exception:
        logger.exception("ONLYOFFICE callback save failed: template_id=%s url=%s", template_id, download_url)
        return {"error": 1}
    return {"error": 0}