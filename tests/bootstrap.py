from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text

from src.infra.db import Base, engine, init_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_MODE_ENV = "MAILING_AGENT_TEST_MODE"
TEST_DATABASE_ENV = "MAILING_AGENT_TEST_DATABASE"


def test_runtime_root() -> Path:
    raw = os.environ.get("MAILING_AGENT_TEST_RUNTIME", "").strip()
    if raw:
        return Path(raw)
    return PROJECT_ROOT / ".test-runtime"


def assert_test_database_is_safe() -> None:
    test_mode = os.environ.get(TEST_MODE_ENV, "").strip().lower()
    if test_mode not in {"1", "true", "yes"}:
        raise RuntimeError(
            f"Refusing to reset a database without {TEST_MODE_ENV}=1. "
            "Run tests through docker-compose.test.yml."
        )

    actual_name = str(engine.url.database or "").strip()
    expected_name = os.environ.get(TEST_DATABASE_ENV, "mailing_test").strip()
    if not expected_name.endswith("_test"):
        raise RuntimeError(
            f"{TEST_DATABASE_ENV} must end with '_test', got {expected_name!r}."
        )
    if actual_name != expected_name or not actual_name.endswith("_test"):
        raise RuntimeError(
            "Refusing to reset a non-test database: "
            f"actual={actual_name!r}, expected={expected_name!r}."
        )


def reset_test_database() -> None:
    assert_test_database_is_safe()
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
