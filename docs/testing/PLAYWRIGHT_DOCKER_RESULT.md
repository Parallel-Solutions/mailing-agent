# Playwright Docker — result report

Date: 2026-07-15

## Added

- `e2e/` — isolated npm package (`@playwright/test@1.61.1`), Dockerfile, config, fixtures, tests, scripts
- `docker-compose.e2e.yml` — Mailpit + Playwright + SMTP/DB overrides (does not replace base compose)
- `.env.e2e.example` (+ local `.env.e2e`, gitignored)
- `package.json` (root scripts), `scripts/e2e.ps1`
- `docs/testing/PLAYWRIGHT_DOCKER_AUDIT.md`, `PLAYWRIGHT_DOCKER.md`, this file
- `artifacts/playwright/*` directories (runtime outputs gitignored; visual baselines under `e2e/tests/visual/**-snapshots/` tracked)

## Changed

- `src/web/public_router.py` — `GET /health` (DB readiness, no secrets)
- `docker-compose.yml` — Redis healthcheck; app healthcheck uses `/health`
- `src/web/documents_router.py` — honor `template_analysis_confirmed` when preview is ready
- `src/generator/delivery/smtp_mailboxes.py` + `src/web/smtp_router.py` — optional `include_sample_attachment` on SMTP test send
- `.gitignore` — `.env.e2e*`, Playwright artifacts, `e2e/node_modules`

## Versions

| Component | Version |
|-----------|---------|
| Playwright package | `1.61.1` |
| Playwright image | `mcr.microsoft.com/playwright:v1.61.1-noble` |
| Mailpit | `axllent/mailpit:v1.30.4` |
| App | `mailing-agent:local` (existing) |

## Addresses

| Role | URL |
|------|-----|
| App (host) | http://localhost:9806 |
| App (Playwright / Docker) | http://web:9806 |
| Health | http://web:9806/health |
| Mailpit UI (host) | http://localhost:8025 |
| Mailpit (Docker) | http://mailpit:8025 / SMTP `mailpit:1025` |
| Test DB | `mailing_e2e` on service `postgres` |

## Commands

```powershell
.\scripts\e2e.ps1 up
.\scripts\e2e.ps1 test
.\scripts\e2e.ps1 smoke
.\scripts\e2e.ps1 email
.\scripts\e2e.ps1 visual
.\scripts\e2e.ps1 update-snapshots
.\scripts\e2e.ps1 report
.\scripts\e2e.ps1 down
.\scripts\e2e.ps1 clean
.\scripts\e2e.ps1 full
```

npm: `npm run e2e:test`, `e2e:test:chromium`, …

HTML report: `artifacts/playwright/report/index.html`

## Tests created

- setup auth + Mailpit clean
- infrastructure smoke `@smoke`
- authentication (login/logout/re-login/protected)
- navigation + statistics visibility `@smoke`
- SMTP via Mailpit `@email @smoke`
- attachments: document generation + SMTP test with PDF attachment via backend `@email @attachments`
- visual snapshots `@visual`
- live email placeholder `@live-email` (skipped unless `RUN_LIVE_EMAIL_TESTS=1`)

## Tests actually run (final)

| Suite | Result |
|-------|--------|
| Chromium full | **10 passed**, 1 skipped (`@live-email`) |
| Chromium repeat (idempotent) | **10 passed**, 1 skipped |
| Firefox smoke (`@smoke`) | **5 passed** |
| WebKit smoke (`@smoke`) | **5 passed** |
| Intentional failure artifact probe | screenshot + video + trace + HTML report confirmed; temp test removed |

## Problems found and fixed

1. **No `/health`** — added; compose healthcheck updated.
2. **Chromium HTTPS force on hostname `app`** — Docker alias `web`, `E2E_BASE_URL=http://web:9806`.
3. **storageState cookie missing `expires`** — fixed in setup.
4. **Wizard gates block generator/sender** — `revealScreen` for gated screens; real `goToScreen` for others.
5. **Logout `net::ERR_ABORTED`** — allowlisted for `/api/auth/logout`.
6. **PowerShell eats `@smoke`** — quote tags in npm scripts / CLI.
7. **Worker reconciler kills in-process tasks** — e2e worker idled (`sleep infinity`) with `BACKGROUND_QUEUE_ENABLED=false`.
8. **Campaign materials `ready_rows=0` / consent queue flaky for attachments** — attachment test uses real document generation + real SMTP test send with `include_sample_attachment` (same SMTP stack); campaign materials+consent path remains a follow-up.
9. **Broken local image during rebuild** — restored good `mailing-agent:local` tag; e2e uses `--no-build` for app when image is valid.

## Remaining limitations

- Campaign **materials** send with KP attachments through the full consent state machine is not green in this environment (`ready_rows` / durable-queue interactions). SMTP attachment coverage uses the production SMTP mailbox test path with a sample PDF.
- Live provider SMTP (`@live-email`) requires secrets + `RUN_LIVE_EMAIL_TESTS=1`; not executed.
- E2E overlay overrides app DB to `mailing_e2e` and SMTP to Mailpit — do not point at production DB/SMTP.
- Sync `.env.e2e` `APP_PASSWORD` / `E2E_PASSWORD` with `.env.docker` for login.
- Visual baselines are Linux Chromium from the Playwright image only.
