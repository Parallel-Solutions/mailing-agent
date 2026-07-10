# Аудит статистики email-рассылок

> Дата аудита: 2026-07-09  
> Аудитор: senior backend/fullstack engineer review  
> Статус: **только чтение**, код не изменялся

---

## 1. Краткий вывод

### Что уже работает
- **Журнал отправок** (`sent_mail_log` в PostgreSQL `job_events`) — фиксируется каждая отправка с полным набором мета-данных, transport, provider_message_id, idempotency_key.
- **Статусы от провайдеров** через webhook-ы: RuSender, MailoPost, UniSender Go (и polling-fallback для UniSender Classic). Статусы: `delivered`, `opened`, `clicked`, `hard_bounced`, `soft_bounced`, `unsubscribed`, `spam/complaint`, `failed`, `rejected`.
- **Нормализация статусов**: технические коды провайдеров → 10 менеджерских статусов (`delivered`, `opened`, `clicked`, `email_broken`, `soft_bounce`, `delivery_error`, `unsubscribed`, `spam`, `pending`, `no_data`).
- **Сценарий согласия на КП** (consent flow): генерация токена, страница подтверждения, автодосылка материалов. Хранится в `consents.json`.
- **Менеджерский дашборд** (`/statistics`): KPI-карточки, воронка, графики, списки работы, экспорт XLSX/CSV/NDJSON.
- **API статистики**: 9 эндпоинтов `/api/sender/*` с фильтрами, пагинацией, авторизацией по job/tenant.
- **Дедупликация webhook-ов**: `event_key` (SHA256 или event_id провайдера) — защита от дублей.
- **Кеш**: 20-секундный in-memory TTL per-job; фоновый refresh провайдерских данных до 25 jobs.
- **Экспорт отчётов**: 4 типа × 3 формата (xlsx, csv, ndjson); история экспортов.
- **Тесты производительности**: `test_statistics_performance.py` проверяет cache TTL, отсутствие N+1.

### Что частично работает
- **SMTP**: письма логируются как `accepted` — финальный статус (`delivered`/`bounced`) никогда не появится без дополнительного bounce-обработчика (IMAP/DSN); в дашборде всё зависает в `pending`.
- **Детальная история получателя**: `build_recipient_detail` возвращает только **один** последний статус, не цепочку событий.
- **`clicked_after_consent`**: в `build_consents_view` жёстко захардкожен `0` — клики после согласия не считаются.
- **Отписки через SMTP**: не реализованы (нет List-Unsubscribe обработчика).

### Чего нет
- **Tracking pixel** для открытий — полная зависимость от провайдерских событий (SMTP — слепой).
- **Click tracking** через собственный прокси — ссылки не оборачиваются.
- **Отдельная таблица событий** в PostgreSQL — события провайдеров в JSONL-файлах, не в БД.
- **Агрегированная таблица статистики** — вся аналитика строится on-the-fly из `sent_mail_log` + JSONL.
- **Bounce-webhook для SMTP** (DSN/NDR обработка).
- **WebSocket/SSE** для live-обновлений — только polling UI каждые 20 минут.
- **Mailpit/MailHog** в docker-compose — нет тестового SMTP-перехватчика.
- **Тестовый endpoint** для имитации webhook без реального провайдера (кроме сырых POST на `/api/webhooks/*`).

### Можно ли тестировать текущую статистику
**Да, частично.** Можно тестировать через реальные провайдеры (RuSender/MailoPost/UniSender) или вручную POSTить webhook-payload на защищённые токеном endpoint-ы. Полноценное автоматическое тестирование ограничено: нет Mailpit, нет тестового SMTP с bounce-симуляцией.

---

## 2. Карта модулей

| Зона | Файлы | Назначение |
|---|---|---|
| Отправка | `src/generator/delivery/sender_agent.py` | Основная логика рассылки: SMTP/RuSender/MailoPost/UniSender; запись `sent_mail_log` |
| Отправка | `src/workers/background_worker.py` | Запуск sender в отдельном процессе/потоке |
| Согласие | `src/generator/delivery/consent_store.py` | Генерация токенов, подтверждение, досылка КП; хранение `consents.json` |
| Webhook RuSender | `src/generator/delivery/rusender_events.py` | Парсинг и сохранение событий RuSender → JSONL |
| Webhook MailoPost | `src/generator/delivery/mailopost_events.py` | Парсинг и сохранение событий MailoPost → JSONL |
| Webhook UniSender Go | `src/generator/delivery/unisender_go_events.py` | Парсинг событий UniSender Go → JSONL; polling fallback |
| Статистика | `src/generator/delivery/manager_stats.py` | Агрегация, нормализация, фильтрация, кеш; все build_* функции |
| Статистика | `src/generator/delivery/sender_report.py` | `_build_delivery_rows`: JOIN sent_mail_log + JSONL провайдеров |
| Действия | `src/generator/delivery/manager_actions.py` | Менеджерские пометки (call, find_email, etc.) → `sender_manager_actions` stream |
| API статистики | `src/web/statistics_router.py` | 9 эндпоинтов `/api/sender/*`; авторизация, фильтры, экспорт |
| API отправки | `src/web/sender_router.py` | `/api/sender/run|status|analytics`; webhook endpoints `/api/webhooks/*` |
| API согласия | `src/web/consent_router.py` | HTML страницы `/consent/request/{token}`, `/consent/confirm/{token}` |
| БД | `src/infra/db.py` | SQLAlchemy engine, Alembic migrations runner |
| БД модели | `src/infra/models.py` | ORM-модели всех таблиц |
| Jobs | `src/jobs/state.py` | Сохранение/загрузка `agent_states` |
| Jobs | `src/jobs/job_docs.py` | `list_job_ids_with_sent_mail()` — discovery кампаний по БД |
| Jobs | `src/jobs/clients_store.py` | Клиентские строки (получатели) |
| Конфиг | `src/utils/config.py` | Все настройки: транспорт, API-ключи, токены webhook |
| Фронтенд | `templates/statistics.html` | SPA страница `/statistics` |
| Фронтенд | `src/web/static/statistics.js` | Логика дашборда, Chart.js графики, фильтры, авторефреш |
| Тесты | `tests/test_sender_webhooks.py` | Auth token, body size limits |
| Тесты | `tests/test_statistics_performance.py` | Cache TTL, N+1, background refresh |
| Тесты | `tests/test_consent_store.py` | Полный сценарий согласия |
| Тесты | `tests/test_sender_agent.py` | Send flows, dedup |
| E2E | `tests/e2e/` | Матричные тесты через реальный API |

---

## 3. Карта таблиц БД

| Таблица | Назначение | Основные поля | Связи | Что пишется |
|---|---|---|---|---|
| **job_events** | Универсальный event-store | `id`, `job_id`, `stream`, `seq`, `payload` (JSONB), `created_at` | → `agent_states.job_id` | Потоки: `sent_mail_log`, `sender_manager_actions`, `sender_reports` |
| **agent_states** | Состояние агента отправки | `job_id`, `agent_name`, `state` (JSONB), `details`, `updated_at` | ← `job_events.job_id` | Статус sender: `idle/running/completed/stopped/error`; meta кампании |
| **job_owners** | Контроль доступа к jobs | `job_id`, `owner_username`, `tenant_id`, ... | → `job_events.job_id` | Привязка job к пользователю/тенанту |
| **clients** | Строки получателей | `job_id`, `row_index`, `data` (JSONB) | → `job_events.job_id` | Excel-строки; поле `data.xlsx` хранит статус отправки |
| **job_docs** | Универсальные JSON-документы | `job_id`, `name`, `payload` | — | Документы job-а |
| **parser_rules** | Правила парсера Excel | — | — | Не связана с delivery stats |
| **parser_errors** | Ошибки парсера | — | — | Не связана с delivery stats |

### Индексы, важные для статистики
- `idx_job_events_job_stream_seq` ON `(job_id, stream, seq)` — основной для поиска send-логов

### Файловое хранилище (вне БД)

| Путь | Содержимое | Назначение |
|---|---|---|
| `{job}/state/consents.json` | Список ConsentRecord | Статус согласий: `pending/request_sent/confirmed/expired`; `materials_status` |
| `{job}/state/rusender_events.jsonl` | NDJSON событий | Webhook payload от RuSender + dedup key |
| `{job}/state/mailopost_events.jsonl` | NDJSON событий | Webhook payload от MailoPost + dedup key |
| `{job}/state/unisender_go_events.jsonl` | NDJSON событий | Webhook/API события UniSender Go + dedup key |
| `{job}/state/sender_delivery_analytics.json` | Snapshot | Кеш аналитики (легаси, сейчас in-memory) |
| `{job}/state/sender_delivery_report.xlsx` | Excel | Экспорт журнала отправок |
| `{job}/state/reports/` | CSV/NDJSON | Экспортированные отчёты менеджера |
| `tmp/rusender_events_unmatched.jsonl` | NDJSON | Несопоставленные webhook-и RuSender |
| `tmp/mailopost_events_unmatched.jsonl` | NDJSON | Несопоставленные webhook-и MailoPost |

---

## 4. Карта событий статистики

| Событие | Поддерживается | Где создается | Где хранится | Комментарий |
|---|---|---|---|---|
| Письмо поставлено в очередь | Частично | `sender_agent.py` | `sent_mail_log` (status=`queued` внутри provider) | Нет отдельного события «queued»; логируется при успешном вызове API |
| Письмо отправлено (принято провайдером) | **Да** | `_append_sent_mail_log()` | `job_events.sent_mail_log` | Пишется сразу после успешного send-вызова |
| Ошибка отправки | **Да** | `_append_sent_mail_log(error=...)` | `job_events.sent_mail_log` | Поле `warning` или `error` в payload |
| Письмо доставлено | **Да** (webhook) | `*_events.py` | `{job}/state/*_events.jsonl` | Только для RuSender/MailoPost/UniSender; SMTP — нет |
| Письмо не доставлено (hard bounce) | **Да** (webhook) | `*_events.py` | `{job}/state/*_events.jsonl` | `hard_bounced`, `err_user_unknown`, `err_user_inactive` |
| Soft bounce | **Да** (webhook) | `*_events.py` | `{job}/state/*_events.jsonl` | `soft_bounced`, `err_will_retry` |
| Ошибка доставки (rejected) | **Да** (webhook) | `*_events.py` | `{job}/state/*_events.jsonl` | `failed`, `rejected`, `err_delivery_failed` |
| Письмо открыто | **Да** (webhook) | `*_events.py` | `{job}/state/*_events.jsonl` | Только если провайдер поддерживает open-tracking |
| Переход по ссылке (click) | **Да** (webhook) | `*_events.py` | `{job}/state/*_events.jsonl` | Только провайдерский click-tracking |
| Согласие на КП (confirm) | **Да** | `consent_router.py → consent_store.confirm_consent()` | `consents.json` | IP, User-Agent, timestamp; статус → `confirmed` |
| Отправка запроса согласия | **Да** | `sender_agent._send_consent_requests_with_transport` | `sent_mail_log` + `consents.json` | status=`request_sent` |
| Досылка КП (materials dispatch) | **Да** | `consent_router._dispatch_materials_after_consent` | `consents.json` (materials_status, materials_sent_at) | Запускает `run_sender` с `require_confirmed_consent=True` |
| Отписка | **Да** (webhook) | `*_events.py` | `{job}/state/*_events.jsonl` | `unsubscribed`, `ok_unsubscribed`; нет системной блокировки повторной отправки |
| Жалоба на спам | **Да** (webhook) | `*_events.py` | `{job}/state/*_events.jsonl` | `spam`, `complaint`, `err_spam_rejected` |
| Повторная отправка | Нет отдельного события | — | — | Повторная отправка создаёт новую запись в `sent_mail_log` |
| Дубль webhook | Защита есть | `event_key` в `*_events.py` | JSONL | SHA256 или event_id; дубли не пишутся |
| Tracking pixel open | **Нет** | — | — | Не реализован custom pixel |
| Click через прокси | **Нет** | — | — | Ссылки не оборачиваются |
| Bounce SMTP (DSN/NDR) | **Нет** | — | — | SMTP всегда `accepted`, bounce необнаруживаем |

---

## 5. Webhook-провайдеры

| Провайдер | Endpoint | События | Проверка подписи | Idempotency | Job matching |
|---|---|---|---|---|---|
| **UniSender Go** | `POST /api/webhooks/unisender-go/{token}` | `delivered`, `opened`, `clicked`, `hard_bounced`, `soft_bounced`, `unsubscribed`, `spam`, `failed`, `rejected`, `processing`, `queued`, `sent`, и UniSender-специфичные `ok_*`/`err_*`/`skip_dup_*` | `secrets.compare_digest(token, settings.unisender_webhook_token)` — 503 если токен не настроен | `event_key` = event_id или SHA256(payload) | `metadata.app_job_id` / `job_id` / `mailing_agent_job_id` из event |
| **RuSender** | `POST /api/webhooks/rusender/{token}` | `external_mail.delivered`, `.hard_bounced`, `.soft_bounced`, `.error`, `.open`, `.click`, `.unsubscribe`, `.complaint` | `secrets.compare_digest(token, settings.rusender_webhook_token)` — 503 если не настроен | `event_key` per event | `task_id` из send_log → JSONL lookup |
| **MailoPost** | `POST /api/webhooks/mailopost/{token}` | `delivered`, `hard_bounced`, `soft_bounced`, `skipped`, `opened`, `clicked`, `unsubscribed`, `complained` | `secrets.compare_digest(token, settings.mailopost_webhook_token)` — 503 если не настроен | `event_key` per event | `message_id` из send_log |
| **UniSender Classic** | Нет webhook; polling через `checkEmail` API | `ok_delivered`, `ok_read`, `ok_link_visited`, `ok_spam_folder`, `ok_unsubscribed`, `err_*` | — | — | `provider_message_id` из send_log |

**Общие ограничения:**
- Макс. размер тела: `settings.webhook_max_body_bytes` (default 256 KB) → 413 при превышении.
- Повторный webhook с тем же `event_key` → запись не создаётся.
- Несопоставленные события → `tmp/*_events_unmatched.jsonl`.

---

## 6. UI/API статистики

### Страницы

| URL | Описание |
|---|---|
| `/statistics` | Основной менеджерский дашборд: KPI-карточки, воронка, графики по статусам/провайдерам/ролям, списки работы (кому звонить, проблемы), инсайты, секции: Campaigns, Recipients, Campaign Analytics, Consents, Email Problems, Reports |
| `/` | Оператор-вид: ссылка на `/statistics`, лёгкий виджет аналитики `/api/sender/analytics` |

### API-эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/sender/manager-dashboard` | Главный дашборд: KPI, воронка, инсайты, работа-листы |
| GET | `/api/sender/campaigns` | Список кампаний с sent/delivered/opened/clicked/consents, rates |
| GET | `/api/sender/recipients` | Paginated список получателей: статус, интерес, рекомендация |
| GET | `/api/sender/recipients/{row_key}` | Детали получателя + история менеджерских действий |
| POST | `/api/sender/recipients/{row_key}/action` | Добавить менеджерское действие (call, find_email, etc.) |
| GET | `/api/sender/consents` | Воронка согласий + список KP-получателей |
| GET | `/api/sender/email-problems` | Hard/soft bounce: причины, домены |
| GET | `/api/sender/campaign-analytics/{job_id}` | Детальная аналитика кампании: daily chart, провайдерская эффективность |
| GET | `/api/sender/reports` | Доступные типы отчётов + история экспортов |
| POST | `/api/sender/reports/export` | Генерация отчёта (xlsx/csv/ndjson) |
| GET | `/api/sender/reports/download/{report_id}` | Скачать файл отчёта |
| GET | `/api/sender/analytics` | Легаси-аналитика для оператора (index.html) |

### Фильтры

`job_id`, `campaign`, `period_from`, `period_to`, `provider`, `providers` (comma-sep), `status`, `recipient_role`, `consent_status`, `manager_action`, `organization`, `problems_only`, `q` (полнотекстовый поиск), `quick_filter` (delivered/opened/clicked/problems/pending/action).

### Что видит менеджер

| Срез | Доступно |
|---|---|
| По одной рассылке | Детальная аналитика: daily chart, провайдерская эффективность, high-interest компании, проблемные адреса |
| По всем рассылкам | Список кампаний со сводкой, общий дашборд |
| По одному получателю | Статус, интерес, рекомендуемое действие, история менеджерских пометок |
| По провайдеру | Фильтр `provider=` в campaigns/recipients; provider_effectiveness в campaign-analytics |
| По ошибкам | `/api/sender/email-problems`: причины bounce, топ-домены |
| По кликам/согласиям | `/api/sender/consents`: воронка согласий, список подтверждённых, `materials_sent` |
| По досылкам КП | В секции consents: `materials_status`, `materials_sent_at` |

---

## 7. Сценарий согласия на КП

Полный путь (letter → link → click → status → follow-up → statistics):

```
1. Отправка запроса согласия
   sender_agent.run_sender(send_mode="consent_request")
   → consent_store.prepare_consent_request(row, recipient)
     → генерируется uuid токен, TTL = 720h
     → URL = {PUBLIC_BASE_URL}/consent/confirm/{token}
     → запись в consents.json: status=pending
   → _send_consent_requests_with_transport(transport=...)
     → письмо уходит через SMTP/RuSender/MailoPost/UniSender
     → _append_sent_mail_log() → job_events.sent_mail_log
   → consent_store.mark_consent_request_sent()
     → consents.json: status=request_sent, request_sent_at=now

2. Получатель кликает по ссылке
   GET /consent/request/{token}  → HTML-страница предпросмотра
   POST /consent/confirm/{token}
   → consent_store.confirm_consent(ip, user_agent)
     → consents.json: status=confirmed, confirmed_at=now, ip, user_agent
   → consent_store.mark_materials_dispatch_started()
     → consents.json: materials_status=queued, materials_dispatch_requested_at=now

3. Background task досылает материалы
   consent_router._dispatch_materials_after_consent(token, background_tasks)
   → run_sender(job_id, row_ids=[row_id], send_mode="materials",
                require_confirmed_consent=True, ...)
     → генерирует PDF (КП / договор / оба)
     → отправляет через провайдер
     → _append_sent_mail_log() → sent_mail_log
   → consent_store.mark_materials_dispatch_result(status="sent"/"error")
     → consents.json: materials_status=sent/error, materials_sent_at=now

4. Recovery loop (при перезапуске)
   main.py: recover_pending_materials_dispatches()
   → каждые 60 сек. ищет consents с materials_status=queued, 
     materials_dispatch_requested_at > STALE_RETRY_SECONDS назад
   → заново запускает _dispatch_materials_after_consent

5. Статистика в UI
   /api/sender/consents → build_consents_view()
   → summary: confirmed, materials_sent, opened_after_consent, clicked_after_consent=0(!)
   → funnel: согласие → отправка → доставка → открытие → клик
```

**Текущие ограничения:**
- `clicked_after_consent` = 0 всегда (захардкожено).
- Согласия хранятся в файлах, не в PostgreSQL → нет транзакционной гарантии.
- Нет API-эндпоинта для проверки состояния согласия по `row_key` (только через `/api/sender/consents`).

---

## 8. Что можно тестировать сейчас

1. **Запись sent_mail_log**: запустить отправку → проверить `SELECT * FROM job_events WHERE stream='sent_mail_log'`.
2. **Webhook delivered (RuSender)**: `POST /api/webhooks/rusender/{token}` с payload → проверить `rusender_events.jsonl`.
3. **Webhook delivered (MailoPost)**: аналогично `/api/webhooks/mailopost/{token}`.
4. **Webhook UniSender Go**: `POST /api/webhooks/unisender-go/{token}`.
5. **Idempotency webhooks**: отправить одинаковый payload дважды → убедиться, что в JSONL одна запись.
6. **Нормализация статусов**: POST с `hard_bounced` → GET `/api/sender/recipients` → manager_status=`email_broken`.
7. **Сценарий согласия**: POST `/consent/confirm/{token}` → проверить `consents.json` → проверить досылку в `sent_mail_log`.
8. **API campaigns**: GET `/api/sender/campaigns` → проверить поля `sent`, `delivered`, `opened`.
9. **Фильтры**: GET `/api/sender/recipients?quick_filter=problems` → только проблемные.
10. **Экспорт отчёта**: POST `/api/sender/reports/export` → GET download → проверить файл.
11. **Auth**: запрос без авторизации → 401/403.
12. **Webhook token auth**: неверный token → 401.
13. **Webhook body size**: тело > 256 KB → 413.
14. **Статус кампании**: `_campaign_status()` при `mode=send, status=completed` → `completed`.

---

## 9. Что нельзя тестировать сейчас

| Ограничение | Причина |
|---|---|
| Доставка через SMTP без реального MX | SMTP не имеет webhook/DSN-обработчика |
| Открытие письма (open event) для SMTP | Нет tracking pixel |
| Click tracking через собственный прокси | Ссылки не оборачиваются |
| `clicked_after_consent` | Жёстко = 0 |
| End-to-end «письмо улетело → доставлено» без реального провайдера | Нет Mailpit/MailHog в docker-compose |
| Автоматическое чтение входящих писем агентом | Нет IMAP-клиента для тестов |
| История событий получателя (timeline) | Только последний статус в UI |
| Запрет повторной отправки после unsubscribe/spam | Нет системной блокировки в `sender_agent` |
| Статистика по шаблонам | Нет группировки по template_name |
| Real-time обновление (push) | Только polling UI |

---

## 10. Рекомендации по тестовому агенту

После аудита видно, что нужен агент со следующими возможностями:

### Endpoint-ы для дёргания
```
POST /api/sender/run                          # запустить тестовую отправку
GET  /api/sender/status                       # дождаться completed
POST /api/webhooks/rusender/{token}           # имитировать delivered/bounced/opened
POST /api/webhooks/mailopost/{token}          # то же для MailoPost
POST /api/webhooks/unisender-go/{token}       # то же для UniSender Go
POST /consent/confirm/{token}                 # имитировать согласие
GET  /api/sender/manager-dashboard            # проверить KPI
GET  /api/sender/campaigns                    # проверить rates
GET  /api/sender/recipients                   # проверить статусы
GET  /api/sender/consents                     # проверить воронку согласий
GET  /api/sender/email-problems               # проверить bounce
POST /api/sender/reports/export               # сгенерировать отчёт
```

### Откуда читать письма
- **Mailpit** (`SMTP_HOST=mailpit, SMTP_PORT=1025`) — перехват SMTP, REST API для чтения входящих.
- Endpoint `GET http://mailpit:8025/api/v1/messages` → список писем.
- Извлечь ссылки из HTML-тела письма (согласие, отписка).

### Какие события проверять
1. `sent_mail_log` в PostgreSQL — для каждой отправки.
2. `*_events.jsonl` — после имитации webhook.
3. `consents.json` — статус согласия и `materials_status`.
4. `/api/sender/manager-dashboard` — сводные KPI.

### Отчёты агента
- JSON/HTML с таблицей: сценарий → ожидаемый результат → фактический результат → passed/failed.
- Сводка: sent, delivered, opened, consents, errors.

### Нужен ли Mailpit
**Да** — обязательно. Без него нельзя проверить реальную отправку SMTP, прочитать ссылку согласия из письма, провести E2E без реальных провайдеров.

### Нужен ли Playwright
**Нет (необязательно)** — достаточно HTTP-клиента (httpx/requests), так как все действия — это API calls + GET ссылок из письма. Playwright нужен только если нужно тестировать UI дашборда (кнопки, фильтры).

---

## 11. Минимальный план внедрения тестового агента

### Этап 1 — Тестовый SMTP в Docker (Mailpit)
```yaml
# Добавить в docker-compose.yml:
mailpit:
  image: axllent/mailpit:latest
  ports:
    - "8025:8025"   # Web UI
    - "1025:1025"   # SMTP
```
Переменные для тестов:
```env
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_USE_TLS=false
SENDER_TRANSPORT=smtp
```

### Этап 2 — Чтение писем агентом
- Mailpit REST API: `GET /api/v1/messages` → список писем.
- Агент извлекает HTML, парсит ссылки (`/consent/confirm/`, `/unsubscribe/`).

### Этап 3 — Проверка ссылок
- Агент кликает по `consent_url` → `POST /consent/confirm/{token}`.
- Проверяет `consents.json` через `/api/sender/consents`.

### Этап 4 — Имитация webhook событий
- Агент читает `provider_message_id` из `sent_mail_log` (через PostgreSQL или API).
- Генерирует webhook payload для каждого провайдера.
- POST на `/api/webhooks/{provider}/{token}`.
- Проверяет обновление статуса через `/api/sender/recipients`.

### Этап 5 — Проверка БД и API
- SQL: `SELECT payload FROM job_events WHERE stream='sent_mail_log' AND job_id=?`
- API: сравнить `dashboard.summary.sent` с количеством строк в `sent_mail_log`.

### Этап 6 — HTML/JSON отчёт
- Агент пишет `test_report_{timestamp}.html` / `.json`.
- Таблица тест-кейсов: ID, сценарий, шаги, ожидаемый результат, фактический, passed/failed.
- Сводка по кампании.
