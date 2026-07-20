from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TRANSPORT = "rusender"


@dataclass(frozen=True)
class ApiClientConfig:
    base_url: str
    username: str
    password: str
    fixtures_dir: Path
    documents_timeout_seconds: float = 1800.0
    sender_timeout_seconds: float = 600.0
    consent_timeout_seconds: float = 300.0
