from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text

from src.infra.db import Base, engine, init_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_root() -> Path:
    raw = os.environ.get("MAILING_AGENT_TEST_RUNTIME", "").strip()
    if raw:
        return Path(raw)
    return PROJECT_ROOT / ".test-runtime"


def reset_test_database() -> None:
    table_names = list(Base.metadata.tables.keys())
    if not table_names:
        return
    quoted = ", ".join(f'"{name}"' for name in table_names)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def bootstrap_test_runtime(*, reset_db: bool = True) -> Path:
    runtime = test_runtime_root()
    storage = runtime / "storage"
    data = runtime / "data"
    logs = runtime / "logs"
    tmp = runtime / "tmp"

    for path in (storage, data, logs, tmp):
        path.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("WORKSPACE_DIR", str(tmp))
    os.environ.setdefault("STORAGE_DIR", str(storage))
    os.environ.setdefault("DATA_DIR", str(data))
    os.environ.setdefault(
        "DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://mailing:mailing@localhost:5432/mailing_test",
        ),
    )
    init_db()
    if reset_db:
        reset_test_database()
    return runtime


@contextmanager
def isolated_auth_db(*, prefix: str = "auth") -> Iterator[Path]:
    runtime = bootstrap_test_runtime(reset_db=True)
    yield runtime / "auth-placeholder"


@contextmanager
def reset_db() -> Iterator[None]:
    reset_test_database()
    try:
        yield
    finally:
        reset_test_database()
