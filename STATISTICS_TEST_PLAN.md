# Тест-план статистики email-рассылок

> Дата: 2026-07-09  
> Версия: 1.0  
> Формат: конкретные тест-кейсы, без реализации нового кода

---

## Предусловия

| # | Требование |
|---|---|
| P1 | Docker Compose запущен: `app`, `postgres`, `minio`, `redis`, `gotenberg` |
| P2 | Пользователь-тестировщик создан, токен авторизации получен |
| P3 | Тестовый job создан через UI или API (`/api/jobs`) |
| P4 | Загружен Excel-файл с тестовыми получателями (1-3 строки) |
| P5 | Провайдер настроен (или `SENDER_TRANSPORT=smtp` + Mailpit) |
| P6 | Webhook-токены настроены: `RUSENDER_WEBHOOK_TOKEN`, `MAILOPOST_WEBHOOK_TOKEN`, `UNISENDER_WEBHOOK_TOKEN` |
| P7 | База данных прогнала миграции (`job_events`, `agent_states` таблицы существуют) |

---

## Тест-кейсы

| ID | Сценарий | Шаги | Ожидаемый результат | Где проверять |
|---|---|---|---|---|
| **A. БАЗОВАЯ ОТПРАВКА** | | | | |
| A-01 | Запись в sent_mail_log после отправки | 1. `POST /api/sender/run` с `job_id`, `send_mode=materials` <br>2. Опрашивать `GET /api/sender/status` до `status=completed` <br>3. `SELECT payload FROM job_events WHERE stream='sent_mail_log' AND job_id=?` | Строки появились; `sent_at` заполнен; `transport` = настроенный провайдер; `provider_message_id` не пустой | PostgreSQL `job_events` |
| A-02 | Статус получателя = pending сразу после отправки | 1. Выполнить A-01 <br>2. `GET /api/sender/recipients?job_id=<job>` | `manager_status.key` = `"pending"` или `"no_data"` для каждого получателя; `sent_at` заполнен | `/api/sender/recipients` |
| A-03 | KPI-карточка Отправлено = числу строк в Excel | 1. Выполнить A-01 <br>2. `GET /api/sender/manager-dashboard?job_id=<job>` | `summary.sent` = числу строк; `summary.delivered` = 0; `cards[0].value` = числу строк | `/api/sender/manager-dashboard` |
| A-04 | Кампания появляется в списке кампаний | 1. Выполнить A-01 <br>2. `GET /api/sender/campaigns` | Кампания присутствует; `sent` > 0; `period_from` заполнен | `/api/sender/campaigns` |
| **B. ОШИБКА ОТПРАВКИ** | | | | |
| B-01 | Ошибка SMTP → статус delivery_error | 1. Установить `SMTP_HOST=127.0.0.1, SMTP_PORT=9999` (недоступный хост) <br>2. `POST /api/sender/run` <br>3. Дождаться `completed` или `error` | `sent_mail_log` содержит запись с полем `warning` или `error`; `provider.status` = `"error"` / пустой | `job_events`, `/api/sender/recipients` |
| B-02 | Статус получателя = delivery_error после ошибки | 1. Выполнить B-01 <br>2. Вручную создать webhook-payload `{"status":"failed","message_id":"..."}` <br>3. `POST /api/webhooks/rusender/{token}` <br>4. `GET /api/sender/recipients` | `manager_status.key` = `"delivery_error"` | `/api/sender/recipients` |
| **C. ДОСТАВКА ЧЕРЕЗ WEBHOOK** | | | | |
| C-01 | RuSender webhook delivered | 1. Выполнить A-01, получить `provider_message_id` из `sent_mail_log` <br>2. `POST /api/webhooks/rusender/{token}` с `{"events":[{"trigger":"external_mail.delivered","task_id":"<provider_message_id>","created_at":"..."}]}` <br>3. `GET /api/sender/recipients?job_id=<job>` | Запись появилась в `rusender_events.jsonl`; `manager_status.key` = `"delivered"` | `{job}/state/rusender_events.jsonl`, `/api/sender/recipients` |
| C-02 | MailoPost webhook delivered | 1. Аналогично C-01 для MailoPost <br>2. `POST /api/webhooks/mailopost/{token}` с `{"event":"delivered","message_id":"...","at":"..."}` | `mailopost_events.jsonl` обновлён; статус = `"delivered"` | `{job}/state/mailopost_events.jsonl`, `/api/sender/recipients` |
| C-03 | UniSender Go webhook delivered | 1. Аналогично C-01 <br>2. `POST /api/webhooks/unisender-go/{token}` с `{"events":[{"event_name":"delivered","job_id":"...","metadata":{"app_job_id":"<job_id>"}}]}` | `unisender_go_events.jsonl` обновлён; статус = `"delivered"` | `{job}/state/unisender_go_events.jsonl`, `/api/sender/recipients` |
| C-04 | Изменение KPI после webhook delivered | 1. Выполнить C-01 <br>2. `GET /api/sender/manager-dashboard?job_id=<job>` | `summary.delivered` увеличился на 1; `rates.delivery_rate` > 0 | `/api/sender/manager-dashboard` |
| **D. ОТКРЫТИЕ ПИСЬМА** | | | | |
| D-01 | RuSender webhook open → статус opened | 1. Выполнить C-01 (delivered) <br>2. `POST /api/webhooks/rusender/{token}` с `{"trigger":"external_mail.open","task_id":"...","created_at":"..."}` <br>3. `GET /api/sender/recipients` | `manager_status.key` = `"opened"` | `/api/sender/recipients` |
| D-02 | MailoPost webhook opened | Аналогично D-01, событие `"event":"opened"` | `manager_status.key` = `"opened"` | `/api/sender/recipients` |
| D-03 | Статус не регрессирует: opened не меняется на delivered | 1. Отправить webhook `opened` <br>2. Отправить webhook `delivered` (повторно) <br>3. Проверить статус | Статус остаётся `"opened"` (приоритет выше) | `/api/sender/recipients` |
| D-04 | Tracking pixel (SMTP) — не реализован | 1. Отправить через SMTP <br>2. Проверить HTML тела письма | Pixel-тег `<img src="...track...">` отсутствует → тест фиксирует ограничение | Тело письма в Mailpit |
| **E. КЛИК ПО ССЫЛКЕ** | | | | |
| E-01 | RuSender webhook click → статус clicked | 1. Выполнить A-01 <br>2. `POST /api/webhooks/rusender/{token}` с `{"trigger":"external_mail.click","task_id":"...","created_at":"...","url":"https://..."}` | `manager_status.key` = `"clicked"`; `interest.key` = `"high"` | `/api/sender/recipients` |
| E-02 | MailoPost webhook clicked | Аналогично E-01 с `"event":"clicked"` | `manager_status.key` = `"clicked"` | `/api/sender/recipients` |
| E-03 | Организация появляется в work_list.interested | 1. Выполнить E-01 <br>2. `GET /api/sender/manager-dashboard` | `work_lists.interested` содержит организацию из тестовой строки | `/api/sender/manager-dashboard` |
| **F. СОГЛАСИЕ НА КП** | | | | |
| F-01 | Отправка запроса согласия | 1. `POST /api/sender/run` с `send_mode=consent_request` <br>2. Дождаться `completed` | `sent_mail_log` содержит запись с `send_mode=consent_request`; в `consents.json` статус = `"request_sent"` | `job_events`, `consents.json` |
| F-02 | Получение страницы предпросмотра согласия | 1. Выполнить F-01 <br>2. Извлечь `token` из `consents.json` <br>3. `GET /consent/request/{token}` | HTTP 200; HTML содержит кнопку подтверждения | HTTP response |
| F-03 | Подтверждение согласия | 1. Выполнить F-02 <br>2. `POST /consent/confirm/{token}` | HTTP 200; `consents.json`: `status=confirmed`, `confirmed_at` заполнен, `ip` заполнен; `materials_status=queued` | `consents.json` |
| F-04 | Consent API отражает подтверждение | 1. Выполнить F-03 <br>2. `GET /api/sender/consents?job_id=<job>` | `summary.confirmed` = 1; строка получателя с `consent_status_key=confirmed` | `/api/sender/consents` |
| F-05 | Просроченный токен отклоняется | 1. Создать запись согласия с `expires_at` в прошлом (модифицировать `consents.json` вручную) <br>2. `POST /consent/confirm/{token}` | HTTP 4xx или страница с сообщением об истечении срока | HTTP response |
| **G. ДОСЫЛКА КП (MATERIALS DISPATCH)** | | | | |
| G-01 | Автодосылка КП после согласия | 1. Выполнить F-03 <br>2. Дождаться до 10 сек (background task) <br>3. `SELECT payload FROM job_events WHERE stream='sent_mail_log' AND job_id=?` (проверить новую запись) | В `sent_mail_log` появилась запись с `send_mode=materials` для того же `row_id`; `consents.json`: `materials_status=sent`, `materials_sent_at` заполнен | `job_events`, `consents.json` |
| G-02 | Дашборд materials_sent = 1 | 1. Выполнить G-01 <br>2. `GET /api/sender/manager-dashboard?job_id=<job>` | `summary.materials_sent` = 1 | `/api/sender/manager-dashboard` |
| G-03 | Recovery досылки при перезапуске | 1. Выполнить F-03 (materials_status=queued) <br>2. Перезапустить контейнер app <br>3. Дождаться recovery poll (60 сек) | `consents.json`: `materials_status=sent` | `consents.json` |
| G-04 | Двойная досылка не происходит | 1. Выполнить G-01 <br>2. Повторно `POST /consent/confirm/{token}` | Страница показывает "Согласие уже подтверждено"; `sent_mail_log` не содержит ещё одной записи с тем же `row_id` + `send_mode=materials` | HTTP response, `job_events` |
| **H. ОТПИСКА** | | | | |
| H-01 | RuSender webhook unsubscribe | 1. Выполнить A-01 <br>2. `POST /api/webhooks/rusender/{token}` с `{"trigger":"external_mail.unsubscribe","task_id":"...","created_at":"..."}` | `manager_status.key` = `"unsubscribed"`; `recommended_action.key` = `"do_not_contact"` | `/api/sender/recipients` |
| H-02 | Получатель c unsubscribed не блокируется системой | 1. Выполнить H-01 <br>2. Попробовать снова `POST /api/sender/run` | **ОЖИДАЕТСЯ ОГРАНИЧЕНИЕ**: система НЕ блокирует повторную отправку автоматически; фиксировать как known gap | `/api/sender/run`, `sent_mail_log` |
| **I. ЖАЛОБА НА СПАМ** | | | | |
| I-01 | RuSender webhook complaint → spam | 1. Выполнить A-01 <br>2. `POST /api/webhooks/rusender/{token}` с `{"trigger":"external_mail.complaint","task_id":"...","created_at":"..."}` | `manager_status.key` = `"spam"` | `/api/sender/recipients` |
| I-02 | Email problems содержит жалобу | 1. Выполнить I-01 <br>2. `GET /api/sender/email-problems?job_id=<job>` | Получатель присутствует; `bounce_reason_label` = "Блокировка как спам" | `/api/sender/email-problems` |
| **J. ДУБЛИ WEBHOOKS (IDEMPOTENCY)** | | | | |
| J-01 | Дублирующий RuSender webhook не создаёт вторую запись | 1. Сохранить точный JSON первого успешного webhook delivered <br>2. `POST /api/webhooks/rusender/{token}` с тем же payload повторно | В `rusender_events.jsonl` ровно одна запись с данным `event_key`; статус получателя не изменился | `{job}/state/rusender_events.jsonl` |
| J-02 | Дублирующий MailoPost webhook | Аналогично J-01 для MailoPost | Одна запись в `mailopost_events.jsonl` | `{job}/state/mailopost_events.jsonl` |
| J-03 | Дублирующий UniSender Go webhook | Аналогично J-01 для UniSender Go | Одна запись в `unisender_go_events.jsonl` | `{job}/state/unisender_go_events.jsonl` |
| **K. BOUNCE** | | | | |
| K-01 | Hard bounce → email_broken | 1. Выполнить A-01 <br>2. `POST /api/webhooks/rusender/{token}` с `{"trigger":"external_mail.hard_bounced","task_id":"..."}` | `manager_status.key` = `"email_broken"`; `bounce_reason` = `"email_not_exists"` | `/api/sender/recipients` |
| K-02 | Soft bounce → soft_bounce | Аналогично K-01, `"trigger":"external_mail.soft_bounced"` | `manager_status.key` = `"soft_bounce"`; `bounce_reason` = `"temporary_error"` | `/api/sender/recipients` |
| K-03 | Email problems summary | 1. Выполнить K-01, K-02 <br>2. `GET /api/sender/email-problems?job_id=<job>` | `summary.hard_bounce` = 1; `summary.soft_bounce` = 1; `summary.problem_addresses` = 2 | `/api/sender/email-problems` |
| **L. АГРЕГАЦИЯ СТАТИСТИКИ** | | | | |
| L-01 | Dashboard совпадает с БД | 1. Выполнить несколько сценариев (sent, delivered, opened) <br>2. `SELECT count(*) FROM job_events WHERE stream='sent_mail_log' AND job_id=?` <br>3. `GET /api/sender/manager-dashboard?job_id=<job>` | `dashboard.summary.sent` = COUNT из БД | `/api/sender/manager-dashboard`, PostgreSQL |
| L-02 | Campaign rates корректны | 1. Выполнить: 3 sent, 2 delivered, 1 opened <br>2. `GET /api/sender/campaigns?job_id=<job>` | `delivery_rate` ≈ 66.7%; `open_rate` ≈ 50.0%; `ctr` = 0% | `/api/sender/campaigns` |
| L-03 | Campaign-analytics daily chart | 1. Разнести отправки по 2 дням <br>2. `GET /api/sender/campaign-analytics/{job_id}` | `daily` содержит 2 записи с корректными датами и счётчиками | `/api/sender/campaign-analytics` |
| **M. АВТОРИЗАЦИЯ** | | | | |
| M-01 | Запрос без токена → 401/403 | `GET /api/sender/campaigns` без заголовка Authorization | HTTP 401 или 403 | HTTP status code |
| M-02 | Webhook с неверным токеном → 401 | `POST /api/webhooks/rusender/WRONG_TOKEN` | HTTP 401 | HTTP status code |
| M-03 | Webhook без настроенного токена → 503 | Убрать `RUSENDER_WEBHOOK_TOKEN` из env <br>2. `POST /api/webhooks/rusender/anything` | HTTP 503 с сообщением "отключён" | HTTP status code |
| M-04 | Доступ к чужому job → запрещён | `GET /api/sender/campaigns?job_id=<чужой_job>` под другим пользователем | HTTP 403 | HTTP status code |
| **N. ЭКСПОРТ** | | | | |
| N-01 | Экспорт XLSX delivery_summary | 1. `POST /api/sender/reports/export` `{"report_type":"delivery_summary","fmt":"xlsx","job_id":"..."}` <br>2. Из ответа взять `report_id` <br>3. `GET /api/sender/reports/download/{report_id}` | HTTP 200; файл `.xlsx` скачивается; содержит строки с получателями | HTTP response, xlsx file |
| N-02 | Экспорт CSV consents | Аналогично N-01 с `"report_type":"consents","fmt":"csv"` | `.csv` с полями organization, contact, email, consent_status_label | csv file |
| N-03 | Экспорт NDJSON email_problems | Аналогично N-01 с `"report_type":"email_problems","fmt":"ndjson"` | `.ndjson`; каждая строка — валидный JSON с bounce_reason | ndjson file |
| N-04 | Экспорт отображается в истории | 1. Выполнить N-01 <br>2. `GET /api/sender/reports?job_id=<job>` | `history` содержит запись с `report_id`, `format=xlsx`, `status=ready` | `/api/sender/reports` |
| **O. ПРОИЗВОДИТЕЛЬНОСТЬ** | | | | |
| O-01 | Dashboard < 2 сек при 200 кампаниях | `GET /api/sender/manager-dashboard` при наличии 200 jobs в БД (или через существующий тест `test_statistics_performance.py`) | Response time < 2000 ms | `/api/sender/manager-dashboard` + time measurement |
| O-02 | Кеш работает: повторный запрос быстрее | `GET /api/sender/recipients` дважды подряд | Второй запрос < 100 ms (in-memory cache hit) | Time measurement |
| **P. WEBHOOK BODY SIZE** | | | | |
| P-01 | Тело > 256 KB → 413 | `POST /api/webhooks/rusender/{token}` с телом 300 KB | HTTP 413 | HTTP status code |

---

## Матрица покрытия событий

| Событие | Тест-кейс | Провайдер | Статус |
|---|---|---|---|
| Отправлено | A-01 | Все | Тестируется |
| Доставлено | C-01..C-03 | RuSender/MailoPost/UniSender | Тестируется |
| Не доставлено (hard bounce) | K-01 | RuSender | Тестируется |
| Soft bounce | K-02 | RuSender | Тестируется |
| Delivery error | B-02 | RuSender | Тестируется |
| Открыто | D-01..D-02 | RuSender/MailoPost | Тестируется |
| Клик | E-01..E-02 | RuSender/MailoPost | Тестируется |
| Запрос согласия отправлен | F-01 | Все | Тестируется |
| Согласие подтверждено | F-03 | — | Тестируется |
| Досылка КП | G-01 | Все | Тестируется |
| Отписка | H-01 | RuSender | Тестируется |
| Жалоба/спам | I-01 | RuSender | Тестируется |
| Tracking pixel open (SMTP) | D-04 | SMTP | **НЕ РЕАЛИЗОВАН** |
| Click через прокси (SMTP) | — | SMTP | **НЕ РЕАЛИЗОВАН** |
| `clicked_after_consent` | — | — | **ЗАХАРДКОЖЕН = 0** |
| Блокировка повторной отправки после unsubscribe | H-02 | — | **KNOWN GAP** |

---

## Как запускать тесты

### С реальным провайдером (ручной режим)
```bash
# 1. Поднять стек
docker compose build app && docker compose up -d

# 2. Получить токен авторизации
curl -X POST http://localhost:9806/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<pass>"}'

# 3. Запустить тестовую отправку (подставить job_id)
curl -X POST http://localhost:9806/api/sender/run \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"job_id":"<job_id>","send_mode":"materials"}'

# 4. Имитировать webhook delivered (RuSender)
curl -X POST http://localhost:9806/api/webhooks/rusender/<token> \
  -H "Content-Type: application/json" \
  -d '{"events":[{"trigger":"external_mail.delivered","task_id":"<provider_message_id>","created_at":"2026-07-09T12:00:00Z"}]}'

# 5. Проверить статистику
curl http://localhost:9806/api/sender/manager-dashboard?job_id=<job_id> \
  -H "Authorization: Bearer <token>"
```

### С Mailpit (тестовый SMTP, рекомендуется для E2E)
```bash
# Добавить в docker-compose.yml сервис mailpit (см. STATISTICS_AUDIT.md раздел 11)
# Установить env:
#   SMTP_HOST=mailpit, SMTP_PORT=1025, SENDER_TRANSPORT=smtp

# После отправки читать письма через:
curl http://localhost:8025/api/v1/messages
```

### Через существующие unit-тесты
```bash
# Запустить все unit/integration тесты
docker compose -f docker-compose.test.yml up --abort-on-container-exit

# Запустить конкретный тест
docker compose -f docker-compose.test.yml run --rm app \
  python -m pytest tests/test_sender_webhooks.py -v
```

---

## External-first testing

> Тесты ниже проверяют **полный путь** до внешних систем.  
> Подробная спецификация — в `EXTERNAL_STATISTICS_TEST_PLAN.md`.

### Принцип

Статистика считается корректной только если она совпадает между четырьмя источниками:

1. **Наше приложение** — `sent_mail_log`, `*_events.jsonl`, `consents.json`, `/api/sender/*`
2. **Внешний провайдер** — RuSender / MailoPost / UniSender Go / UniSender Classic
3. **Реальные webhook события** — delivered, opened, clicked, hard_bounced, soft_bounced, unsubscribed, spam
4. **Реальный тестовый mailbox** — письмо пришло, ссылки рабочие, досылка получена

### Уровни external тестов

| Уровень | Описание | Требует публичный URL | Требует mailbox |
|---|---|---|---|
| L0 | Local preflight (Mailpit) | Нет | Нет |
| L1 | Real provider send + provider_message_id | Нет | Нет |
| L2 | Real webhook callback | **Да** | Нет |
| L3 | Real mailbox verification | **Да** | **Да** |
| L4 | Reconciliation (сверка всех источников) | **Да** | **Да** |

### Краткий перечень external test-cases

| ID | Сценарий | Уровень | Провайдер |
|---|---|---|---|
| EXT-SEND-01 | RuSender real send + provider_message_id | L1 | RuSender |
| EXT-SEND-02 | MailoPost real send + provider_message_id | L1 | MailoPost |
| EXT-SEND-03 | UniSender Go real send + metadata.app_job_id | L1 | UniSender Go |
| EXT-SEND-04 | UniSender Classic real send + polling | L1 | UniSender Classic |
| EXT-WEBHOOK-01 | RuSender реальный delivered webhook | L2 | RuSender |
| EXT-WEBHOOK-02 | MailoPost реальный delivered webhook | L2 | MailoPost |
| EXT-WEBHOOK-03 | UniSender Go реальный delivered webhook | L2 | UniSender Go |
| EXT-OPEN-01 | Open tracking через реальный mailbox | L2+L3 | RuSender |
| EXT-CLICK-01 | Click tracking через реальный mailbox | L2+L3 | RuSender |
| EXT-CONSENT-01 | Consent flow через реальное письмо | L3 | Любой |
| EXT-FOLLOWUP-01 | Досылка КП после consent | L3 | Любой |
| EXT-BOUNCE-01 | Hard bounce (sandbox address) | L2 | RuSender |
| EXT-BOUNCE-02 | Soft bounce (sandbox only) | L2 | MailoPost |
| EXT-UNSUB-01 | Unsubscribe event | L2 | RuSender |
| EXT-SPAM-01 | Spam/complaint (provider sandbox only) | L2 | Любой |
| EXT-IDEM-01 | Повторный реальный webhook — дубль не создаётся | L2 | RuSender |
| EXT-RECON-01 | Полная сверка provider ↔ app ↔ mailbox | L4 | Все |

### Ограничения безопасности

- Использовать только тестовые адреса, принадлежащие нам.
- Не отправлять письма реальным клиентам.
- Не имитировать согласие реальных людей.
- Не делать массовые отправки (максимум 5–10 писем в тестовой серии).
- Для bounce/spam использовать только sandbox-механизм провайдера или безопасные тестовые адреса.
- Если безопасного механизма нет — тест помечается **«manual / provider sandbox only»**.

### Env-переменные для external тестов

```env
# Провайдер
SENDER_TRANSPORT=rusender

# RuSender
RUSENDER_API_KEY=<ключ>
RUSENDER_SENDER_EMAIL=<email>
RUSENDER_WEBHOOK_TOKEN=<токен>

# MailoPost
MAILOPOST_API_TOKEN=<токен>
MAILOPOST_SENDER_EMAIL=<email>
MAILOPOST_WEBHOOK_TOKEN=<токен>

# UniSender Go
UNISENDER_API_KEY=<ключ>
UNISENDER_API_BASE_URL=https://goapi.unisender.ru/ru/transactional/api/v1
UNISENDER_SENDER_EMAIL=<email>
UNISENDER_WEBHOOK_TOKEN=<токен>

# Публичный URL (обязателен для webhook, Level 2+)
PUBLIC_BASE_URL=https://staging.example.com

# Тестовый mailbox (для Level 3)
IMAP_HOST=imap.mail.ru
IMAP_PORT=993
IMAP_USE_SSL=true
```

### Known gaps (зафиксированные ограничения)

| # | Gap | Где проявляется |
|---|---|---|
| 1 | `clicked_after_consent` = 0 (захардкожено) | `/api/sender/consents` → `summary.clicked_after_consent` |
| 2 | Нет блокировки повторной отправки после unsubscribe/spam | `sender_agent.run_sender` — не проверяет статус получателя |
| 3 | SMTP без bounce-обработчика — статус всегда `pending` | SMTP transport; нет DSN/NDR обработчика |
| 4 | История событий получателя — только последний статус | `build_recipient_detail` — один статус, не цепочка |
| 5 | Нет click proxy — клики только провайдерские | Ссылки в письмах не оборачиваются в прокси |
