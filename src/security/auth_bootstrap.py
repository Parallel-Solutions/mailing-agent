from __future__ import annotations

from typing import Any

from src.security.auth import configured_auth_users
from src.security.user_store import configure_auth_db, sync_imported_user


def bootstrap_auth_store(settings_obj: Any) -> None:
    db_path = getattr(settings_obj, "auth_db_path", "storage/auth/auth.sqlite")
    configure_auth_db(db_path)

    for username, config in configured_auth_users(settings_obj).items():
        if username and config.get("password"):
            sync_imported_user(
                username,
                str(config["password"]),
                tenant_id=str(config.get("tenant_id") or username),
                role=str(config.get("role") or "user"),
            )
