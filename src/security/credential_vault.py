from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from src.utils.config import settings


class CredentialVaultError(RuntimeError):
    pass


def _fernet() -> Fernet:
    raw_key = str(settings.smtp_credentials_key or "").strip()
    if not raw_key:
        raise CredentialVaultError("SMTP_CREDENTIALS_KEY не настроен.")
    if len(raw_key) == 44 and raw_key.endswith("="):
        try:
            return Fernet(raw_key.encode("ascii"))
        except ValueError as exc:
            raise CredentialVaultError("SMTP_CREDENTIALS_KEY имеет неверный формат Fernet.") from exc
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        raise CredentialVaultError("Пустой секрет нельзя сохранить.")
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise CredentialVaultError("Пустой зашифрованный секрет.")
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialVaultError("Не удалось расшифровать секрет.") from exc
