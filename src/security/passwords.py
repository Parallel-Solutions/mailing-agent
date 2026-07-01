from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    raw = str(password or "").encode("utf-8")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(str(password or "").encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
