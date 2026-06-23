# Security and Architecture Remediation Plan


Статусы:

- `todo` - задача еще не начата.
- `in_progress` - задача в работе.
- `implemented` - код/документация изменены.
- `verified` - исправление проверено тестами или ручной проверкой.
- `blocked` - нужен внешний ответ, доступ или продуктово-юридическое решение.

## Phase 0: Safety Gate

| ID | Status | Severity | Issue | Target files | Verification |
| --- | --- | --- | --- | --- | --- |
| SG-01 | verified | Critical | Запретить запуск с пустым `APP_PASSWORD`. | `src/utils/config.py`, `main.py`, `.env.example`, `README.md`, `tests/test_app_security.py` | `.\.venv\Scripts\python.exe -m unittest tests.test_app_security -v` - OK, 2 tests. |
| SG-02 | verified | Critical | Убрать arbitrary PID kill: нельзя останавливать произвольный PID от клиента. | `src/web/workers_router.py`, `src/workers/process_manager.py`, `tests/test_worker_process_manager.py` | `python -m pytest -p no:cacheprovider --basetemp C:\tmp\pytest-worker tests/test_worker_process_manager.py` - 6 passed. |
| SG-03 | verified | Critical | Сделать confirmed consent обязательным для любой отправки материалов. | `src/generator/delivery/sender_agent.py`, `tests/test_sender_agent.py` | `.\.venv\Scripts\python.exe -m unittest tests.test_sender_agent -v` - OK, 19 tests. |
| SG-04 | verified | Critical | GET consent не должен подтверждать согласие или запускать отправку; POST должен быть идемпотентным при повторах и гонках. | `src/web/consent_router.py`, `src/generator/delivery/consent_store.py`, `tests/test_consent_store.py` | `.\.venv\Scripts\python.exe -m unittest tests.test_consent_store -v` - OK, 6 tests. |
| SG-05 | verified | High | Webhooks: добавить защиту от подделки/replay и лимит тела. | `src/web/sender_router.py`, `src/generator/delivery/unisender_go_events.py`, `src/generator/delivery/rusender_events.py`, `tests/test_sender_webhooks.py` | `.\.venv\Scripts\python.exe -m unittest tests.test_sender_webhooks -v` - OK, 4 tests; `.\.venv\Scripts\python.exe -m unittest tests.test_unisender_go_events tests.test_rusender_events -v` - OK, 7 tests. |
| SG-06 | verified | High | Убрать fallback parser downloads на глобальный output. | `src/web/download_router.py`, `tests/test_download_router.py` | `.\.venv\Scripts\python.exe -m unittest tests.test_download_router -v` - OK, 4 tests. |
| SG-07 | blocked | High | SSRF protection для scraper/browser tools. Парсер сейчас вне scope: код ведет другой специалист, по решению владельца не трогаем. | `src/parser_new/tools/scraper_tool.py`, `src/parser_new/tools/email_tool.py`, `src/parser_new/tools/search_tool.py` | Отложено до согласования с владельцем parser-кода. |
| SG-08 | verified | High | Детерминированные provider idempotency keys. | `src/generator/delivery/sender_agent.py`, `tests/test_sender_agent.py` | `.\.venv\Scripts\python.exe -m py_compile src\generator\delivery\sender_agent.py tests\test_sender_agent.py` - OK; `.\.venv\Scripts\python.exe -m unittest tests.test_sender_agent -v` - OK, 22 tests; full touched suite OK, 45 tests. |
| SG-09 | blocked | High | Удалить tracked runtime artifacts и проверить риск утечки данных. Non-parser cleanup сделан; оставшиеся tracked Excel/SQLite относятся к parser-owned зоне или parser справочникам и не трогаются без владельца parser-кода. | `.gitignore`, `README.md`, tracked `*.xlsx`, `src/parser_new/memory/agent.db` | `data (2).xlsx` и `service_docs/unisender_report_2026-05-29.xlsx` сняты с tracking через `git rm --cached`; локальные копии сохранены. Остались `service_docs/base.xlsx`, `service_docs/RMZ7KH.xlsx`, `src/parser_new/memory/agent.db`, `src/parser_new/output/archive/*.xlsx` - blocked by parser ownership. |
| SG-10 | verified | High | Добавить минимальный API security test suite. | `tests/test_api_security_suite.py`, existing security tests | Single controlled suite covers auth config/principals, tenant access, audit logging, worker stop guards, consent confirmation semantics, webhooks, download isolation, request validation, safe 500 details, upload signatures, and sender consent/idempotency. |

## Phase 1: Multi-User Access Model

| ID | Status | Severity | Issue | Target files | Verification |
| --- | --- | --- | --- | --- | --- |
| MU-01 | verified | Critical | Ввести понятие current user/tenant в auth layer. | `src/security/auth.py`, `main.py`, `src/utils/config.py`, `.env.example`, `README.md`, `tests/test_multi_user_access.py` | `.\.venv\Scripts\python.exe -m unittest tests.test_multi_user_access -v` - OK, 6 tests. |
| MU-02 | verified | Critical | Добавить object-level authorization для `job_id`. | `src/jobs/access.py`, `src/web/*_router.py`, `tests/test_multi_user_access.py` | Tenant isolation tests cover same-tenant access, cross-tenant denial, history filtering, and worker stop denial. |
| MU-03 | verified | High | `/api/jobs/history` должен возвращать только jobs текущего пользователя/tenant. | `src/web/jobs_router.py`, `tests/test_multi_user_access.py` | `test_jobs_history_filters_to_current_tenant` - OK. |
| MU-04 | verified | High | Consent token должен быть привязан к job/tenant/recipient и иметь expiry. | `src/generator/delivery/consent_store.py`, `src/web/consent_router.py`, `tests/test_multi_user_access.py`, `tests/test_consent_store.py` | Consent tests cover owner scope, recipient key, expiry denial, POST idempotency, and dispatch-once behavior. |
| MU-05 | verified | Medium | Audit log для опасных действий. | `src/jobs/audit.py`, `src/web/*_router.py`, `tests/test_multi_user_access.py` | `test_append_audit_event_writes_actor_and_action` - OK; dangerous start/stop/consent/upload paths call audit helper. |

## Phase 2: API Contracts and Validation

| ID | Status | Severity | Issue | Target files | Verification |
| --- | --- | --- | --- | --- | --- |
| API-01 | verified | Medium | Заменить raw `dict` body на Pydantic request models. | `src/web/request_models.py`, `src/web/*_router.py`, `tests/test_api_request_validation.py`, `tests/test_reliability_state.py`, `tests/test_worker_process_manager.py` | `tests.test_api_request_validation` OK, 5 tests via normalized-env runner wrapper; static checks OK; parser internals untouched. |
| API-02 | verified | Medium | Унифицировать response shape. | `src/web/responses.py`, `src/web/generator_router.py`, `src/web/jobs_router.py`, `src/web/sender_router.py`, `src/web/documents_router.py`, `src/web/philologist_router.py`, `tests/test_api_response_contracts.py` | Transitional contract verified: affected non-parser endpoints now return `{status,result}` while preserving legacy top-level fields for UI compatibility. Parser routes are deferred by owner request. |
| API-03 | verified | Medium | Скрыть internal exception details из HTTP 500. | `src/web/errors.py`, `src/web/documents_router.py`, `src/web/sender_router.py`, `tests/test_api_error_contracts.py` | `tests.test_api_error_contracts` + adjacent API contract suites OK, 16 tests via normalized-env runner wrapper; parser routes deferred by owner request. |
| API-04 | verified | Medium | Усилить upload validation. | `src/web/upload_validation.py`, `main.py`, `tests/test_upload_validation.py` | Invalid renamed XLSX/DOCX uploads are rejected before save/background work; valid OOXML signatures still upload. |

## Phase 3: Reliability and State

| ID | Status | Severity | Issue | Target files | Verification |
| --- | --- | --- | --- | --- | --- |
| RS-01 | verified | Medium | Единый atomic JSON writer для consent/webhook/state. | `src/jobs/json_store.py`, `src/jobs/state.py`, `src/jobs/access.py`, `src/workers/*`, delivery event stores | `py_compile` OK; `tests.test_reliability_state`, consent/webhook touched suite OK. |
| RS-02 | verified | Medium | Corrupt state не должен молча сбрасываться в empty state. | `src/jobs/state.py`, `tests/test_reliability_state.py` | Corrupt JSON returns `status=error` with `state_error`, path and summary; original corrupt file is not overwritten by `load_agent_state`. |
| RS-03 | verified | High | Не удалять output folders по широкому row-id prefix во время генерации. | `src/generator/generation/generator_agent.py`, `src/generator/generation/document_builder.py`, `src/workers/background_worker.py`, `main.py` | Test covers `ID=1` not deleting `ID=1_2`; cleanup now uses exact folder names or output manifest. |
| RS-04 | verified | Medium | Перенести долгий parser run из sync API в worker/background flow. | `src/web/parser_router.py`, `src/workers/background_worker.py`, `main.py`, `templates/index.html` | API tests cover async start/run scheduling and existing worker reuse; `import main` OK; parser internals untouched. |

## Phase 4: Performance and Operations


| ID | Status | Severity | Issue | Target files | Verification |
| --- | --- | --- | --- | --- | --- |
| PO-01 | todo | Medium | Индексировать/кешировать job history вместо полного scan. | `src/web/jobs_router.py` | Benchmark/test на большое число jobs. |
| PO-02 | todo | Medium | Не собирать большие ZIP синхронно на download path. | `src/web/download_router.py`, archive worker | API test: stale archive triggers background build or returns clear 409/202. |
| PO-03 | todo | Medium | Оптимизировать Excel read/write modes. | `src/generator/generation/excel_io.py`, parser modules | Performance regression tests or benchmark notes. |

## Phase 5: Architecture Cleanup


| ID | Status | Severity | Issue | Target files | Verification |
| --- | --- | --- | --- | --- | --- |
| AC-01 | todo | Medium | Разгрузить root `main.py` на app factory/router wiring modules. | `main.py`, `src/web/app.py` | Import/startup tests. |
| AC-02 | todo | Medium | Разбить `sender_agent.py` на transport, selection, consent, state, report units. | `src/generator/delivery/*` | Existing sender tests pass; new focused tests. |
| AC-03 | todo | Medium | Разбить `philologist_agent.py` на orchestration/state/fix/report units. | `src/generator/philologist/*` | Existing philologist tests pass. |
| AC-04 | todo | Medium | Вынести inline JS из `templates/index.html` и убрать unsafe HTML restore. | `templates/index.html`, `src/web/static/*` | UI smoke/manual test; XSS regression tests where practical. |
| AC-05 | todo | Medium | Убрать service-locator globals из web services. | `src/web/documents_service.py`, `src/web/sender_service.py` | Unit tests instantiate services with explicit deps. |

## Work Log

| Date | ID | Status | Summary | Verification |
| --- | --- | --- | --- | --- |
| 2026-06-21 | PLAN-01 | implemented | Создан remediation plan по итогам аудита. | Read-only review context; код сервиса не изменялся. |
| 2026-06-21 | SG-01 | verified | Добавлен fail-fast guard: startup и auth path отклоняют запуск/доступ при пустом `APP_PASSWORD`; README и `.env.example` уточняют обязательные auth-настройки. | `.\.venv\Scripts\python.exe -m unittest tests.test_app_security -v` - OK, 2 tests; full touched suite OK, 42 tests. |
| 2026-06-21 | SG-02 | verified | Убран fallback `os.kill(pid)`: `/api/workers/stop` теперь требует `status_path` внутри `jobs_dir`, а `pid` используется только как sanity-check активного процесса. | `python -m pytest -p no:cacheprovider --basetemp C:\tmp\pytest-worker tests/test_worker_process_manager.py` - 6 passed. |
| 2026-06-21 | SG-03 | verified | `run_sender` теперь принудительно требует confirmed consent для любого `send_mode="materials"`, даже если caller не передал `require_confirmed_consent`. Добавлен regression test на materials bypass. | `.\.venv\Scripts\python.exe -m unittest tests.test_sender_agent -v` - OK, 19 tests. |
| 2026-06-21 | SG-04 | verified | GET consent routes render a confirmation form without mutating state. POST confirmation now updates `consents.json` under per-file locks, writes atomically, dispatches materials once under concurrent confirms, scopes confirmed consent to the requested attachment mode, and does not downgrade confirmed status when request-send bookkeeping arrives late. | `.\.venv\Scripts\python.exe -m unittest tests.test_consent_store -v` - OK, 6 tests; full touched suite OK, 40 tests. |
| 2026-06-21 | SG-05 | verified | Webhooks now reject invalid tokens, enforce `WEBHOOK_MAX_BODY_BYTES` before JSON parsing, and skip duplicate UniSender Go/RuSender provider events using stable replay keys. | `.\.venv\Scripts\python.exe -m unittest tests.test_sender_webhooks -v` - OK, 4 tests; `.\.venv\Scripts\python.exe -m unittest tests.test_unisender_go_events tests.test_rusender_events -v` - OK, 7 tests. |
| 2026-06-21 | SG-06 | verified | Parser result/failed downloads with `job_id` now search only that job output directory and no longer fall back to legacy/global parser latest output. Legacy fallback remains only for requests without `job_id`. | `.\.venv\Scripts\python.exe -m unittest tests.test_download_router -v` - OK, 4 tests. |
| 2026-06-21 | SG-08 | verified | Provider idempotency keys for RuSender and UniSender Go are now deterministic from provider/job/send_run/row/recipient/send_mode/attachment_mode; sent-mail logs expose `provider_idempotency_key` for audit/debugging. | `.\.venv\Scripts\python.exe -m py_compile src\generator\delivery\sender_agent.py tests\test_sender_agent.py` - OK; `.\.venv\Scripts\python.exe -m unittest tests.test_sender_agent -v` - OK, 22 tests; full touched suite OK, 45 tests. |
| 2026-06-21 | MU-01..MU-05 | verified | Phase 1 Multi-User Access Model implemented: Basic auth now returns `Principal(username, tenant_id, role)`, jobs get owner metadata, tenant-scoped object authorization guards web `job_id` endpoints, job history/latest-data are filtered by tenant, consent records carry owner/tenant/recipient/expiry, and dangerous actions write audit events. | `.\.venv\Scripts\python.exe -m py_compile main.py src\security\auth.py src\jobs\access.py src\jobs\audit.py src\web\*.py src\generator\delivery\consent_store.py tests\test_multi_user_access.py` - OK; `.\.venv\Scripts\python.exe -m unittest tests.test_multi_user_access -v` - OK, 6 tests; touched suite OK, 51 tests. |
| 2026-06-21 | RS-01..RS-04 | verified | Phase 3 Reliability and State implemented: shared JSON/JSONL helpers, explicit corrupt-state diagnostics, safe output cleanup by exact folder/manifest, parser start/run moved to subprocess worker flow with UI async polling. Parser internals under `src/parser` and `src/parser_new` were not modified. | `py_compile` changed files OK; `tests.test_reliability_state` OK, 5 tests; touched suite OK, 52 tests; `tests.test_multi_user_access` OK, 6 tests; `import main` OK; `git diff --check` OK; `git diff --name-only -- src\\parser src\\parser_new` empty. |
| 2026-06-21 | API-01 | verified | API command endpoints now use Pydantic request models for job-scoped, chat, prompt, limit, sender run, documents start, philologist run, load-test, data-verify, inflection approval, and worker stop payloads. Existing UI field names are preserved; invalid limits/PIDs/unknown fields are rejected before business actions. | `tests.test_api_request_validation` OK, 5 tests via normalized-env runner wrapper; `rg` shows no route-boundary `payload: dict`; `git diff --check` OK; `git diff --name-only -- src\parser src\parser_new` empty. |
| 2026-06-22 | API-02 | verified | Added transitional `ok_response` helper and normalized `/api/counts`, `/api/jobs`, `/api/data/info`, `/api/upload/data`, `/api/upload/template`, sender webhook health/token health, and non-parser chat endpoints to include `{status,result}` while keeping existing top-level fields used by the UI. Parser routes and parser internals were not modified. | `py_compile` OK for touched API files; `tests.test_api_response_contracts` + `tests.test_api_request_validation` OK, 12 tests via normalized-env runner wrapper; `git diff --check` OK; `git diff --name-only -- src\parser src\parser_new` empty. |
| 2026-06-22 | API-03 | verified | Added `internal_server_error()` helper and replaced unsafe non-parser HTTP 500 details in documents start, sender run, UniSender Go webhook save, and RuSender webhook save paths. Regression tests verify generic client messages while exception details remain logs-only. Parser routes and parser internals were not modified. | `py_compile` OK for touched API files; `tests.test_api_error_contracts` + `tests.test_api_response_contracts` + `tests.test_api_request_validation` OK, 16 tests via normalized-env runner wrapper; `git diff --check` OK; `git diff --name-only -- src\parser src\parser_new` empty. |
| 2026-06-22 | API-04 | verified | Added centralized upload validation for XLSX/DOCX OOXML signatures and wired `main.py` to use it. Data uploads now reject renamed non-zip files before save/parser verification, and template uploads reject DOCX-like zips missing `word/document.xml`. | `py_compile` OK for touched files; `tests.test_upload_validation` + adjacent API contract suites OK, 20 tests via normalized-env runner wrapper; `git diff --check` OK; `git diff --name-only -- src\parser src\parser_new` empty. |
| 2026-06-22 | SG-10 | verified | Added `tests.test_api_security_suite` as a single minimal security suite for the API boundary and filled a unittest gap for worker-stop status paths outside `jobs_dir`. The suite aggregates existing behavioral coverage for auth, tenant isolation, audit, consent, webhooks, downloads, request validation, safe 500s, upload validation, and sender consent/idempotency. Parser internals were not modified. | `py_compile` OK; `tests.test_api_security_suite` OK, 58 tests via normalized-env runner wrapper; no remaining Python processes; `git diff --check` OK; `git diff --name-only -- src\parser src\parser_new` empty. |
| 2026-06-22 | SG-09 | blocked | Non-parser runtime artifact cleanup done: `data (2).xlsx` and `service_docs/unisender_report_2026-05-29.xlsx` were removed from the git index with `git rm --cached` while preserving local files. `.gitignore` now covers temp pytest dirs and SQLite/runtime memory files, and README documents Excel/SQLite artifact hygiene. Full SG-09 verification is blocked because remaining tracked Excel/SQLite files are parser-owned or parser-dependent references. Parser internals were not modified. | `git ls-files "*.xlsx" "*.db" "*.sqlite" "*.sqlite3"` now lists only `service_docs/base.xlsx`, `service_docs/RMZ7KH.xlsx`, `src/parser_new/memory/agent.db`, and `src/parser_new/output/archive/*.xlsx`; `git diff --name-only -- src\parser src\parser_new` empty. |
