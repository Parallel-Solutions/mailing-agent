from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_PATH = PROJECT_ROOT / ".env.local"


def read_env_file_value(key_name: str, env_path: Path = LOCAL_ENV_PATH) -> Optional[str]:
    """Read one value from the project-level .env.local file."""

    value: str | None = None
    try:
        if not env_path.exists():
            return None
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            raw_key, raw_value = line.split("=", 1)
            if raw_key.strip() == key_name:
                value = raw_value.strip().strip('"').strip("'")
    except OSError:
        return None
    return value


def resolve_env_value(key_name: str, default: str | None = None, *, prefer_file: bool = True) -> str | None:
    """Resolve configuration consistently from root .env.local, then process env."""

    if prefer_file:
        file_value = read_env_file_value(key_name)
        if file_value is not None:
            return file_value

    direct_value = os.environ.get(key_name)
    if direct_value is not None:
        return direct_value

    if not prefer_file:
        file_value = read_env_file_value(key_name)
        if file_value is not None:
            return file_value

    return default
