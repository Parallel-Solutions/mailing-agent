from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from src.security.user_store import configure_auth_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_root() -> Path:
    raw = os.environ.get("MAILING_AGENT_TEST_RUNTIME", "").strip()
    if raw:
        return Path(raw)
    return PROJECT_ROOT / ".test-runtime"


def bootstrap_test_runtime() -> Path:
    runtime = test_runtime_root()
    storage = runtime / "storage"
    data = runtime / "data"
    logs = runtime / "logs"
    auth_dir = storage / "auth"
    jobs_dir = storage / "jobs"

    for path in (storage, data, logs, auth_dir, jobs_dir):
        path.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("STORAGE_DIR", str(storage))
    os.environ.setdefault("DATA_DIR", str(data))
    auth_db_path = os.environ.get("AUTH_DB_PATH", str(auth_dir / "auth.sqlite"))
    os.environ.setdefault("AUTH_DB_PATH", auth_db_path)
    configure_auth_db(Path(auth_db_path))
    return runtime


@contextmanager
def isolated_auth_db(*, prefix: str = "auth") -> Iterator[Path]:
    temp_root = test_runtime_root() / f"{prefix}-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=True)
    db_path = temp_root / "auth.sqlite"
    configure_auth_db(db_path)
    try:
        yield db_path
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
