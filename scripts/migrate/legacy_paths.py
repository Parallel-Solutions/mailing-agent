from __future__ import annotations

import os
from pathlib import Path

from src.jobs.storage import JOBS_DIR, STORAGE_DIR


def legacy_jobs_dir() -> Path | None:
    raw = os.getenv("LEGACY_JOBS_DIR", "").strip()
    return Path(raw) if raw else None


def legacy_auth_db_path() -> Path:
    explicit = os.getenv("LEGACY_AUTH_DB", "").strip()
    if explicit:
        return Path(explicit)
    storage_root = os.getenv("LEGACY_STORAGE_DIR", "").strip()
    if storage_root:
        return Path(storage_root) / "auth" / "auth.sqlite"
    return STORAGE_DIR / "auth" / "auth.sqlite"


def legacy_parser_db_path(default: Path) -> Path:
    explicit = os.getenv("LEGACY_PARSER_DB", "").strip()
    if explicit:
        return Path(explicit)
    return default


def iter_jobs_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()
    for candidate in (JOBS_DIR, legacy_jobs_dir()):
        if candidate is None or not candidate.exists():
            continue
        key = str(candidate.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen.add(key)
        dirs.append(candidate)
    return dirs
