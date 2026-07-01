from __future__ import annotations

from typing import Any

from src.security.auth import configured_auth_users
from src.security.user_store import configure_auth_db, has_admin_user, import_user_if_missing


def bootstrap_auth_store(settings_obj: Any) -> None:
    db_path = getattr(settings_obj, "auth_db_path", "storage/auth/auth.sqlite")
    configure_auth_db(db_path)

    if not has_admin_user():
        admin_username = str(getattr(settings_obj, "app_username", "admin") or "admin").strip() or "admin"
        admin_password = str(getattr(settings_obj, "app_password", "") or "").strip()
        admin_tenant = str(getattr(settings_obj, "app_admin_tenant_id", "admin") or "admin").strip() or "admin"
        if admin_password:
            import_user_if_missing(
                admin_username,
                admin_password,
                tenant_id=admin_tenant,
                role="admin",
            )

    for username, config in configured_auth_users(settings_obj).items():
        if username and config.get("password"):
            import_user_if_missing(
                username,
                str(config["password"]),
                tenant_id=str(config.get("tenant_id") or username),
                role=str(config.get("role") or "user"),
            )
