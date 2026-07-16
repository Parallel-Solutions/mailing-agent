# MASTER PLAN — CampaignFlow UI + backend parity

## Current architecture

- **Backend:** FastAPI (`main.py`), Python 3.11, SQLAlchemy + Alembic, PostgreSQL 16
- **Workers:** `src/workers/queue_worker.py` + `background_worker.py` on `background_tasks`
- **Storage:** MinIO (S3), Redis (parser), Gotenberg (DOCX→PDF)
- **Legacy UI:** `templates/index.html` + `src/web/static/*.js` (vanilla)
- **Target UI:** React SPA in `frontend/`, served from same origin `:9806`

## Data model (bridge)

Campaigns are first-class DB entities linked to existing `job_id` for document generation, sender_agent, and manager_stats.

| Entity | Purpose |
|--------|---------|
| `user_profiles` | Profile, defaults, notifications |
| `audiences` / `audience_members` | Saved recipient lists |
| `mail_templates` / `template_versions` | Email/KP/contract templates |
| `campaigns` | Draft → scheduled → running → … |
| `campaign_recipients` | Per-campaign recipients |
| `campaign_schedules` | Windows, batch size, limits |
| `campaign_batches` | Timed send packages |
| `delivery_attempts` | Idempotent send attempts |

## Existing APIs (preserved)

Auth, jobs, documents, generator, philologist, parser, sender, SMTP, statistics (`/api/sender/*`), consent, downloads, preview.

## New APIs

Documented in `docs/api/NEW_UI_API.md` under `/api/v1/*`.

## Docker services

`app`, `worker`, `postgres`, `minio`, `redis`, `gotenberg`×2, `mailpit` (dev/e2e), `playwright` (profile).

## Document generation

Existing `document_builder` / adaptive templates / Gotenberg — reused via `job_id` bridge.

## Sending & schedule

`campaign_batches` → `BackgroundTask` (`sender_batch`) with `available_at`. Pause/resume/cancel at campaign level. Idempotency via `delivery_attempts` + sent_mail_log.

## Statistics

Manager stats APIs unchanged. Dashboard embeds adapted React port of `statistics.js` + Active Sending block from campaign batch API.

## Stitch → routes

| Route | Screen |
|-------|--------|
| `/` | Dashboard / statistics |
| `/campaigns/new` | Create campaign |
| `/campaigns` | List |
| `/campaigns/:id` | Detail / queue |
| `/templates` | Templates |
| `/audiences` | Audiences |
| `/connections` | Connections |
| `/profile` | Profile |

## Migration stages

0. Docs + characterization  
1. Frontend scaffold + SPA serving  
2. Backend domain + migrations  
3. Batch scheduler  
4. SPA screens  
5. Seed + manual docs  
6. `dev.ps1` / `qa.ps1`  
7. Playwright parity  
8. Final gate + `FINAL_RESULT.md`

## Test strategy

Characterization → BE unit → FE unit → API integration → Playwright (Chromium/Firefox/WebKit) → Mailpit SMTP → visual → `@live-email` optional.

## Completion criteria

See user brief §34: new UI default, real batch sending, Mailpit verified, consent E2E, green QA, manual ACCESS.
