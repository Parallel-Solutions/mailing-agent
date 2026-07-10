from __future__ import annotations

import bcrypt


# A fixed bcrypt hash used to normalize verification time when the target user
# does not exist, mitigating username-enumeration via timing side channels.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())


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


def dummy_verify_password() -> None:
    """Perform a throwaway bcrypt check to keep auth timing uniform.

    Call this on the "user not found" branch so that a missing user takes
    roughly the same time as a wrong password for an existing user.
    """
    try:
        bcrypt.checkpw(b"dummy-password-check", _DUMMY_HASH)
    except ValueError:
        pass
