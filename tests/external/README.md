# External statistics integration tests

End-to-end tests that exercise the real delivery/statistics pipeline against
**live email providers** (RuSender / MailoPost / UniSender Go / SMTP), real
webhooks, and (optionally) a real IMAP mailbox.

> WARNING: These tests send real emails. Only ever use test recipient
> addresses that you own. They are disabled by default and are NOT part of the
> normal `python -m tests` suite.

## Safety gate

Every test module calls `require_ext_enabled()` and is skipped unless
`EXT_STATS_ENABLED=1` is set. The runner (`run_external_tests`) exits with
code `2` if the flag is missing. An always-on, non-sending smoke test
(`tests/test_external_harness_smoke.py`) keeps the harness importable and the
skip guard verified inside the default suite.

## Test levels

| Level  | Module                              | What it checks                                   | Extra requirements |
|--------|-------------------------------------|--------------------------------------------------|--------------------|
| send   | `test_ext_send.py`                  | Real provider send + `sent_mail_log`             | provider API key   |
| webhook| `test_ext_webhook.py`               | Real inbound provider webhook -> event JSONL     | public URL + token |
| mailbox| `test_ext_mailbox.py`               | Email arrives in a real IMAP mailbox; link parse | IMAP credentials   |
| bounce | `test_ext_bounce.py`                | Hard/soft bounce classification                  | provider sandbox   |
| recon  | `test_ext_reconciliation.py`        | Provider API status vs. local aggregates         | provider API key   |

Levels self-disable when their credentials are absent (`skip_mailbox`,
`skip_reconciliation`).

## Running

```bash
# Level 1 only (real provider send):
EXT_STATS_ENABLED=1 \
E2E_BASE_URL=http://localhost:9806 \
E2E_USERNAME=admin \
E2E_PASSWORD=... \
EXT_JOB_ID=job-... \
EXT_TEST_EMAIL=you@example.com \
EXT_TRANSPORT=rusender \
RUSENDER_API_KEY=... \
python -m tests.external.run_external_tests --level send

# Everything:
EXT_STATS_ENABLED=1 ... python -m tests.external.run_external_tests --level all
```

Reports (JSON + Markdown) are written to `tests/external/out/`.

## Environment variables

See the module docstrings for the full list:

- App / auth: `E2E_BASE_URL`, `E2E_USERNAME`, `E2E_PASSWORD`
- Test job: `EXT_JOB_ID` (pre-existing job with test recipients), `EXT_TEST_EMAIL` / `EXT_TEST_EMAILS`
- Transport: `EXT_TRANSPORT` (`rusender` | `mailopost` | `unisender` | `smtp`)
- Webhook (Level 2): `EXT_PUBLIC_BASE_URL`, `EXT_RUSENDER_WEBHOOK_TOKEN`, `EXT_MAILOPOST_WEBHOOK_TOKEN`, `EXT_UNISENDER_WEBHOOK_TOKEN`
- Mailbox (Level 3): `EXT_IMAP_HOST`, `EXT_IMAP_PORT`, `EXT_IMAP_USER`, `EXT_IMAP_PASSWORD`, `EXT_IMAP_USE_SSL`
- Reconciliation (Level 4): `EXT_RUSENDER_API_KEY`, `EXT_MAILOPOST_API_TOKEN`, `EXT_UNISENDER_API_KEY`
- Timeouts: `EXT_SENDER_TIMEOUT_SECONDS`, `EXT_WEBHOOK_WAIT_SECONDS`, `EXT_MAILBOX_WAIT_SECONDS`, `EXT_FOLLOWUP_WAIT_SECONDS`
