from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

WORK_TYPES: tuple[str, ...] = (
    "mngp_settlements",
    "mngp_districts",
    "stp_mo",
    "random_forest",
    "territorial_zone_boundaries",
)

DOCUMENT_MODES: tuple[str, ...] = ("kp", "both", "contract")

KP_VARIANTS: tuple[str, ...] = ("kp_1.docx", "kp_2.docx", "kp_3.docx")

SEND_MODES: tuple[str, ...] = ("consent_request", "materials")

RECIPIENT_STRATEGIES: tuple[str, ...] = ("all", "primary_then_fallback")

TRANSPORT = "rusender"


@dataclass(frozen=True)
class E2EConfig:
    base_url: str
    username: str
    password: str
    fixtures_dir: Path
    send_pause_seconds: float
    parallel_jobs: int
    documents_timeout_seconds: float
    sender_timeout_seconds: float
    consent_timeout_seconds: float
    out_dir: Path
    filter_work_type: str | None
    filter_document_mode: str | None
    filter_kp_variant: str | None
    filter_send_mode: str | None
    filter_recipient_strategy: str | None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return Path(raw)


def _optional_env(name: str) -> str | None:
    raw = os.environ.get(name, "").strip()
    return raw or None


def load_config() -> E2EConfig:
    fixtures_default = PROJECT_ROOT / "tests" / "e2e" / "fixtures"
    out_default = PROJECT_ROOT / "tests" / "e2e" / "out"
    return E2EConfig(
        base_url=os.environ.get("E2E_BASE_URL", "http://localhost:9806").rstrip("/"),
        username=os.environ.get("E2E_USERNAME", os.environ.get("APP_USERNAME", "admin")),
        password=os.environ.get("E2E_PASSWORD", os.environ.get("APP_PASSWORD", "change-me")),
        fixtures_dir=_env_path("E2E_FIXTURES_DIR", fixtures_default),
        send_pause_seconds=_env_float("E2E_SEND_PAUSE_SECONDS", 10.0),
        parallel_jobs=max(1, _env_int("E2E_PARALLEL_JOBS", 1)),
        documents_timeout_seconds=_env_float("E2E_DOCUMENTS_TIMEOUT_SECONDS", 1800.0),
        sender_timeout_seconds=_env_float("E2E_SENDER_TIMEOUT_SECONDS", 600.0),
        consent_timeout_seconds=_env_float("E2E_CONSENT_TIMEOUT_SECONDS", 300.0),
        out_dir=_env_path("E2E_OUT_DIR", out_default),
        filter_work_type=_optional_env("E2E_FILTER_WORK_TYPE"),
        filter_document_mode=_optional_env("E2E_FILTER_DOCUMENT_MODE"),
        filter_kp_variant=_optional_env("E2E_FILTER_KP_VARIANT"),
        filter_send_mode=_optional_env("E2E_FILTER_SEND_MODE"),
        filter_recipient_strategy=_optional_env("E2E_FILTER_RECIPIENT_STRATEGY"),
    )


def require_real_e2e_enabled() -> None:
    if os.environ.get("RUN_REAL_E2E", "").strip() != "1":
        raise SystemExit(
            "E2E send matrix is disabled. Set RUN_REAL_E2E=1 to run real RuSender delivery tests.\n"
            "Example:\n"
            "  RUN_REAL_E2E=1 python -m tests.e2e.run_send_matrix"
        )


def fixture_path(config: E2EConfig, name: str) -> Path:
    path = config.fixtures_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")
    return path
