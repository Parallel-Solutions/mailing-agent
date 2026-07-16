"""ONLYOFFICE integration for editable DOCX template versions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

import httpx

from src.campaigns import template_service


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
    if template.get("template_type") not in {"kp", "contract"}:
        raise ValueError("В ONLYOFFICE редактируются только исходники DOCX")
    version = template.get("version") or {}
    filename = str(version.get("filename") or "")
    version_id = str(version.get("id") or "")
    if not filename.lower().endswith(".docx") or not version_id:
        raise ValueError("Для документа необходимо загрузить файл DOCX")

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
        "config": {
            "documentType": "word",
            "type": "desktop",
            "document": {
                "fileType": "docx",
                "key": version_id.replace("-", ""),
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
                "customization": {
                    "autosave": True,
                    "forcesave": True,
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


def source_file(template_id: str, token: str) -> dict[str, Any]:
    payload = _decode_token(token, template_id)
    return template_service.get_template_version_file(
        template_id,
        str(payload["version_id"]),
        str(payload["owner"]),
    )


def handle_callback(template_id: str, token: str, payload: dict[str, Any]) -> dict[str, int]:
    claims = _decode_token(token, template_id)
    status = int(payload.get("status") or 0)
    if status not in {2, 6}:
        return {"error": 0}
    download_url = str(payload.get("url") or "")
    if not download_url:
        return {"error": 1}
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=10)) as client:
            response = client.get(download_url)
            response.raise_for_status()
        template_service.save_docx_editor_version(
            template_id,
            str(claims["owner"]),
            response.content,
        )
    except Exception:
        return {"error": 1}
    return {"error": 0}