# Playwright Docker audit — mailing-agent

Date: 2026-07-15

## Current architecture

- Monolith FastAPI app (`main:app`) serving UI + API on port **9806**.
- Compose services: `app`, `worker`, `postgres`, `minio`, `redis`, `gotenberg`, `gotenberg-2` (+ migrate profile).
- UI: server-rendered HTML/JS (`templates/`, `src/web/static/`), no separate Node frontend package.
- Package manager (app): **uv** (`pyproject.toml` / `uv.lock`). No root `package.json` historically.
- Auth: cookie session `mailing_agent_session`; users bootstrapped from `APP_USERNAME` / `APP_PASSWORD`.
- DB: PostgreSQL 16, `DATABASE_URL=postgresql+psycopg://mailing:mailing@postgres:5432/mailing`.
- SMTP: env `SMTP_*` + encrypted mailboxes (`SMTP_CREDENTIALS_KEY`); real send gated by `SMTP_ALLOW_REAL_SEND`.
- Existing tests: unittest suite (`docker-compose.test.yml`), RuSender API E2E (`tests/e2e/`), gated Python Playwright UI (`tests/ui/` inside app image).
- No CI workflows found.
- App address (host): `http://localhost:9806`
- App address (Docker network): `http://app:9806` (Playwright uses alias `http://web:9806`)

## Problems found

1. No dedicated HTTP `/health` (compose used DB + TCP socket only) — **fixed** with `/health`.
2. No Mailpit / catch-all SMTP for safe local email assertions.
3. Playwright UI tests ran Chromium from the **app** image, not an isolated Playwright container.
4. No isolated npm Playwright package with matching official Microsoft image.
5. Redis had no healthcheck; E2E needed stronger readiness gates.
6. Existing `.env.e2e.example` targeted RuSender live matrix, not Docker Playwright + Mailpit.

## Chosen E2E structure

```text
e2e/                     isolated npm package (@playwright/test)
docker-compose.e2e.yml   overlay: mailpit + playwright + SMTP/DB overrides
.env.e2e.example         safe Playwright/Mailpit examples
artifacts/playwright/    reports, results, screenshots, videos, traces, auth
```

- Test DB: same Postgres container, separate database **`mailing_e2e`** (created by `ensure_database_exists()` on app startup). Production DB `mailing` is not used by the E2E overlay.
- SMTP for E2E: **Mailpit** `mailpit:1025` / UI+API `http://mailpit:8025`.
- Playwright image: `mcr.microsoft.com/playwright:v1.61.1-noble` matching `@playwright/test@1.61.1`.

## Addresses inside Docker network

| Service   | URL / host |
|-----------|------------|
| App UI/API | `http://web:9806` (alias) / `http://app:9806` |
| Health     | `http://web:9806/health` |
| Mailpit API/UI | `http://mailpit:8025` |
| Mailpit SMTP | `mailpit:1025` |
| Postgres   | `postgres:5432` / DB `mailing_e2e` |
| Redis      | `redis:6379` |
| MinIO      | `http://minio:9000` |

**Important:** inside the Playwright container, `localhost` is the Playwright container itself — always use `http://app:9806`.

## Launch commands

```powershell
.\scripts\e2e.ps1 full
# or
npm run e2e:full
```

See [PLAYWRIGHT_DOCKER.md](./PLAYWRIGHT_DOCKER.md).
