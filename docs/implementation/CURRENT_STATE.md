# CURRENT STATE

Updated: 2026-07-16

## Done

- Unpacked Stitch → `design-input/stitch_olive_mail_crm/`
- `MASTER_PLAN.md`, `CURRENT_STATE.md`, `FINAL_RESULT.md`
- Characterization + campaign/schedule tests under `tests/`
- `frontend/` SPA (Ant Design + ProLayout) default on `:9806`
- Domain migration `0006_campaign_flow_domain` + `/api/v1/*`
- Batch scheduler (`sender_batch`), pause/resume/cancel, active-sending
- Screens: Dashboard, campaigns list/new/detail, templates (Tiptap), audiences, connections, profile, auth
- Seed, fixtures, `ACCESS.md`, manual-testing docs
- `scripts/dev.ps1`, `scripts/qa.ps1`
- Playwright: smoke / chromium / Mailpit / visual / campaign-flow

## Verified

- `.\scripts\qa.ps1 full` → **QA PASSED**
- Backend: 528 OK (2 skipped without git)
- FE unit: 4 passed
- Playwright chromium: 12 passed, 1 skipped (`@live-email`)
- Smoke multi-browser: 13 passed
- Dev stack healthy after reset/seed; SPA login at `/login`

## Tests passing

See `FINAL_RESULT.md` for exact counts per QA step.

## Remaining issues / limits

- Live ESP keys optional (`RUN_LIVE_EMAIL_TESTS`)
- Dashboard is stats API adapter, not full legacy statistics.js UI clone
- Visual tests mask live KPI/table widgets

## Continue from

- Optional: deeper statistics UI parity, dedicated consent Playwright journey, live ESP matrix when keys available
