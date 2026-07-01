from __future__ import annotations

import os
from typing import Optional


def resolve_env_value(key_name: str, default: str | None = None) -> str | None:
    """Resolve configuration from process environment (Docker env_file / compose)."""

    value = os.environ.get(key_name)
    if value is not None:
        return value
    return default
