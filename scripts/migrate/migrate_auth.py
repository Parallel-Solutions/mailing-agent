from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.infra.db import init_db, session_scope
from src.infra.models import Session as SessionModel
from src.infra.models import User


def migrate_auth(auth_db_path: Path | None = None) -> dict[str, int]:
    init_db()
    path = auth_db_path or Path("storage/auth/auth.sqlite")
    if not path.exists():
        return {"users": 0, "sessions": 0, "skipped": 1}

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    users_migrated = 0
    sessions_migrated = 0
    with session_scope() as session:
        for row in connection.execute("SELECT username, password_hash, tenant_id, role, created_at FROM users"):
            if session.get(User, row["username"]) is not None:
                continue
            created_at = datetime.fromisoformat(str(row["created_at"]))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            session.add(
                User(
                    username=row["username"],
                    password_hash=row["password_hash"],
                    tenant_id=row["tenant_id"],
                    role=row["role"],
                    created_at=created_at,
                )
            )
            users_migrated += 1
        for row in connection.execute("SELECT token, username, expires_at, created_at FROM sessions"):
            if session.get(SessionModel, row["token"]) is not None:
                continue
            expires_at = datetime.fromisoformat(str(row["expires_at"]))
            created_at = datetime.fromisoformat(str(row["created_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            session.add(
                SessionModel(
                    token=row["token"],
                    username=row["username"],
                    expires_at=expires_at,
                    created_at=created_at,
                )
            )
            sessions_migrated += 1
    connection.close()
    return {"users": users_migrated, "sessions": sessions_migrated}


if __name__ == "__main__":
    print(migrate_auth())
