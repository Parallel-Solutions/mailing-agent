"""Configuration for external statistics integration tests.

All values are read from environment variables so no secrets are hardcoded.
Set EXT_STATS_ENABLED=1 to enable the tests; without it every test is skipped.

Minimal required env for Level 1 (real provider send):
    EXT_STATS_ENABLED=1
    E2E_BASE_URL=http://localhost:9806
    E2E_USERNAME=admin
    E2E_PASSWORD=...
    EXT_JOB_ID=job-...          # pre-existing job with test recipients uploaded
    EXT_TEST_EMAIL=test@example.com   # allowlisted test address we own

For Level 2 (real webhook):
    EXT_PUBLIC_BASE_URL=https://staging.example.com
    EXT_RUSENDER_WEBHOOK_TOKEN=...
    EXT_MAILOPOST_WEBHOOK_TOKEN=...
    EXT_UNISENDER_WEBHOOK_TOKEN=...

For Level 3 (mailbox):
    EXT_IMAP_HOST=imap.mail.ru
    EXT_IMAP_PORT=993
    EXT_IMAP_USER=test@example.com
    EXT_IMAP_PASSWORD=...
"""
from __future__ import annotations

import os
import unittest
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Guard — all tests are skipped unless this is set.
# ---------------------------------------------------------------------------

EXT_ENABLED_VAR = "EXT_STATS_ENABLED"


def require_ext_enabled() -> None:
    """Call at the top of each test module to skip unless EXT_STATS_ENABLED=1."""
    if os.environ.get(EXT_ENABLED_VAR, "").strip() != "1":
        raise unittest.SkipTest(
            f"External statistics tests are disabled.\n"
            f"Set {EXT_ENABLED_VAR}=1 to run them.\n"
            f"WARNING: These tests send real emails via real providers."
        )


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    return default


def _env_list(name: str) -> list[str]:
    """Comma-separated list from env."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class ExtConfig:
    # --- App ---
    base_url: str
    username: str
    password: str

    # --- Test job ---
    job_id: str                        # pre-existing job with test recipients
    test_emails: list[str]             # allowlisted test addresses we own

    # --- Transport ---
    transport: str                     # rusender | mailopost | unisender | smtp

    # --- Provider tokens (for webhook simulation, Level 1) ---
    rusender_webhook_token: str
    mailopost_webhook_token: str
    unisender_webhook_token: str

    # --- Level 2: public URL ---
    public_base_url: str               # where provider sends real webhooks

    # --- Level 3: mailbox (IMAP) ---
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_use_ssl: bool

    # --- Provider API keys (for Level 4 reconciliation) ---
    rusender_api_key: str
    mailopost_api_token: str
    unisender_api_key: str

    # --- Timeouts ---
    sender_timeout_seconds: float      # wait for sender to complete
    webhook_wait_seconds: float        # wait for real webhook to arrive
    mailbox_wait_seconds: float        # wait for email in mailbox
    followup_wait_seconds: float       # wait for materials dispatch

    # --- Behaviour flags ---
    skip_mailbox: bool                 # skip Level 3 if no IMAP configured
    skip_reconciliation: bool          # skip Level 4 provider API calls
    sandbox_bounce_address: str        # provider bounce test address (if known)
    sandbox_spam_mode: bool            # provider has sandbox for spam events


def load_config() -> ExtConfig:
    imap_host = _env("EXT_IMAP_HOST")
    imap_user = _env("EXT_IMAP_USER")
    imap_password = _env("EXT_IMAP_PASSWORD")
    skip_mailbox = not (imap_host and imap_user and imap_password)

    rusender_api_key = _env("EXT_RUSENDER_API_KEY", _env("RUSENDER_API_KEY"))
    mailopost_api_token = _env("EXT_MAILOPOST_API_TOKEN", _env("MAILOPOST_API_TOKEN"))
    unisender_api_key = _env("EXT_UNISENDER_API_KEY", _env("UNISENDER_API_KEY"))
    skip_reconciliation = not (rusender_api_key or mailopost_api_token or unisender_api_key)

    test_emails = _env_list("EXT_TEST_EMAILS")
    if not test_emails:
        single = _env("EXT_TEST_EMAIL")
        if single:
            test_emails = [single]

    return ExtConfig(
        base_url=_env("E2E_BASE_URL", "http://localhost:9806").rstrip("/"),
        username=_env("E2E_USERNAME", _env("APP_USERNAME", "admin")),
        password=_env("E2E_PASSWORD", _env("APP_PASSWORD", "change-me")),
        job_id=_env("EXT_JOB_ID"),
        test_emails=test_emails,
        transport=_env("EXT_TRANSPORT", _env("SENDER_TRANSPORT", "rusender")),
        rusender_webhook_token=_env("EXT_RUSENDER_WEBHOOK_TOKEN", _env("RUSENDER_WEBHOOK_TOKEN")),
        mailopost_webhook_token=_env("EXT_MAILOPOST_WEBHOOK_TOKEN", _env("MAILOPOST_WEBHOOK_TOKEN")),
        unisender_webhook_token=_env("EXT_UNISENDER_WEBHOOK_TOKEN", _env("UNISENDER_WEBHOOK_TOKEN")),
        public_base_url=_env("EXT_PUBLIC_BASE_URL", _env("PUBLIC_BASE_URL", "")).rstrip("/"),
        imap_host=imap_host,
        imap_port=_env_int("EXT_IMAP_PORT", 993),
        imap_user=imap_user,
        imap_password=imap_password,
        imap_use_ssl=_env_bool("EXT_IMAP_USE_SSL", True),
        rusender_api_key=rusender_api_key,
        mailopost_api_token=mailopost_api_token,
        unisender_api_key=unisender_api_key,
        sender_timeout_seconds=_env_float("EXT_SENDER_TIMEOUT_SECONDS", 300.0),
        webhook_wait_seconds=_env_float("EXT_WEBHOOK_WAIT_SECONDS", 180.0),
        mailbox_wait_seconds=_env_float("EXT_MAILBOX_WAIT_SECONDS", 120.0),
        followup_wait_seconds=_env_float("EXT_FOLLOWUP_WAIT_SECONDS", 60.0),
        skip_mailbox=skip_mailbox,
        skip_reconciliation=skip_reconciliation,
        sandbox_bounce_address=_env("EXT_SANDBOX_BOUNCE_ADDRESS"),
        sandbox_spam_mode=_env_bool("EXT_SANDBOX_SPAM_MODE", False),
    )
