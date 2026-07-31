# External-first тест-план статистики email-рассылок

> Дата: 2026-07-09  
> Версия: 1.0  
> Роль: senior QA automation / backend integration engineer  
> Принцип: статистика считается корректной **только** если совпадает между четырьмя источниками: наше приложение, внешний провайдер, реальные webhook-события, реальный тестовый mailbox.

---

## 1. Цель

Обычные unit/integration тесты проверяют только то, что *наш код корректно обрабатывает условные данные*. Они не дают ответа на вопрос:

> «Действительно ли письмо ушло? Действительно ли провайдер доставил его? Действительно ли webhook попал в правильный JSONL? Действительно ли цифры в dashboard совпадают с данными провайдера?»

Настоящий тест-план описывает полный путь:

```
Наше приложение
  → внешний провайдер (RuSender / MailoPost / UniSender Go / UniSender Classic)
    → webhook/API события провайдера
      → статистика в приложении (JSONL + DB + API)
        → внешний тестовый mailbox (факт доставки, согласие, досылка)
          → менеджерский dashboard (KPI совпадают)
```

**Что проверяем:**
- Письмо реально принято провайдером (не только `accepted` в нашей БД).
- `provider_message_id` / `task_id` сохранён корректно и позволяет сопоставить webhook.
- Реальный webhook от провайдера попадает в нужный `{job}/state/*_events.jsonl`.
- Дедупликация работает при повторных webhook-ах от провайдера.
- Статусы в `/api/sender/recipients` совпадают с данными провайдера.
- KPI в `/api/sender/manager-dashboard` и `/api/sender/campaigns` совпадают с данными провайдера.
- Письмо реально пришло в тестовый mailbox с корректными темой/телом/ссылками.
- Сценарий согласия работает через реальное письмо: страница открывается, досылка уходит через провайдера.
- Расхождения между любыми двумя источниками фиксируются как дефект статистики.

---

## 2. Матрица внешних систем

| Система | Роль в тестах | Что проверяем | Как сверяем |
|---|---|---|---|
| **RuSender** | внешний провайдер отправки и webhook events | send, delivered, open, click, bounce, unsubscribe, complaint | `task_id` в `sent_mail_log` → `rusender_events.jsonl` → dashboard |
| **MailoPost** | внешний провайдер отправки и webhook events | send, delivered, open, click, bounce, unsubscribe, complaint | `message_id` в `sent_mail_log` → `mailopost_events.jsonl` → dashboard |
| **UniSender Go** | внешний провайдер отправки и webhook events | delivered / opened / clicked / bounced / spam / failed | `global_metadata.app_job_id` → `unisender_go_events.jsonl` → dashboard |
| **UniSender Classic** | polling через API (нет webhook) | статусы через `checkEmail` API | `provider_message_id` в `sent_mail_log` → polling → dashboard |
| **Тестовый mailbox** | фактическое получение писем | письмо пришло, subject/body, ссылки рабочие, досылка КП получена | IMAP / mailbox API (Yandex360 / mail.ru / Mailtrap) |
| **App DB** | локальная фиксация событий | `sent_mail_log`, `*_events.jsonl`, `consents.json` | SQL + файловые чеки |
| **App API / UI** | менеджерская статистика | KPI, rates, statuses, consents, email-problems | REST API + dashboard |

---

## 3. Уровни тестирования

### Level 0 — Local preflight (Mailpit)

**Цель:** Проверить только то, что письмо сформировано корректно и `sent_mail_log` заполнен.  
**Инструменты:** Mailpit (`SENDER_TRANSPORT=smtp`, `SMTP_HOST=mailpit`).  
**Что проверяем:**
- HTML-тело письма содержит ссылку согласия.
- Subject корректный.
- `sent_mail_log` содержит запись с `transport=smtp`.
- `provider_message_id` может быть пустым — Mailpit его не возвращает.

**НЕ считается** проверкой доставки, open, click, bounce. Провайдерский webhook не приходит. Этот уровень закрывает только формирование письма.

---

### Level 1 — Real provider send

**Цель:** Убедиться, что провайдер реально принял письмо и вернул корректный идентификатор.  
**Предусловия:**
- `SENDER_TRANSPORT=rusender` / `mailopost` / `unisender`.
- API-ключ провайдера настроен.
- Тестовый адрес в allowlist.

**Что проверяем:**
- Провайдер вернул HTTP 200/2xx.
- В `sent_mail_log` сохранён непустой `provider_message_id` (для RuSender — `task_id`, для MailoPost — `message_id`, для UniSender Go — `job_id`).
- `transport` в `sent_mail_log` совпадает с реальным провайдером.
- Провайдерский dashboard / API показывает отправку.

---

### Level 2 — Real webhook callback

**Цель:** Проверить, что реальный webhook от провайдера принимается, сопоставляется с job, дедуплицируется.  
**Предусловия:**
- Приложение доступно по публичному URL (staging / cloudflare tunnel / ngrok).
- Webhook URL зарегистрирован в кабинете провайдера.
- Токены `RUSENDER_WEBHOOK_TOKEN` / `MAILOPOST_WEBHOOK_TOKEN` / `UNISENDER_WEBHOOK_TOKEN` настроены.

**Что проверяем:**
- Провайдер успешно вызывает наш webhook endpoint.
- Событие попало в правильный JSONL (`rusender_events.jsonl` / `mailopost_events.jsonl` / `unisender_go_events.jsonl`).
- `job_id` в JSONL соответствует нашему job — сопоставление произошло через `task_id` (RuSender) / `message_id` (MailoPost) / `metadata.app_job_id` (UniSender Go).
- Повторный webhook от провайдера с тем же payload → дубль не создался, счётчик `duplicates` > 0.
- Несопоставленные события идут в `tmp/*_events_unmatched.jsonl`.

---

### Level 3 — Real mailbox verification

**Цель:** Убедиться, что письмо реально пришло в тестовый mailbox и содержит корректное содержимое.  
**Предусловия:** Доступ к тестовому mailbox через IMAP или API (Mailtrap / mail.ru API / Yandex).

**Что проверяем:**
- Письмо найдено по subject + to.
- HTML-тело содержит ссылку согласия `{PUBLIC_BASE_URL}/consent/confirm/{token}`.
- Ссылка открывается (HTTP 200).
- После подтверждения согласия второе письмо с материалами КП реально пришло в mailbox.

---

### Level 4 — Reconciliation (сверка всех источников)

**Цель:** Убедиться, что данные во всех четырёх источниках совпадают.  
**Шаги:**
1. Провести серию: N sent → дождаться webhook delivered / opened / clicked.
2. Получить статистику из провайдерского dashboard / API.
3. Получить статистику из `GET /api/sender/manager-dashboard`.
4. Получить статистику из `GET /api/sender/campaigns`.
5. Проверить `sent_mail_log` (SQL) и `*_events.jsonl` (файлы).
6. Проверить фактический mailbox.

**Допустимые расхождения:** задержка webhook (секунды-минуты). Устойчивое расхождение (>5 минут после события) записывается как дефект статистики.

---

## 4. Исследование проекта: ключевые переменные и механизмы

### 4.1. Env-переменные по провайдерам

| Переменная | Провайдер | Назначение | Примечание |
|---|---|---|---|
| `SENDER_TRANSPORT` | Все | Выбор транспорта: `smtp`, `rusender`, `mailopost`, `unisender` | Умолчание: `smtp` |
| `SMTP_ALLOW_REAL_SEND` | SMTP | Разрешить реальную SMTP-отправку | По умолчанию `false` — блокирует отправку |
| `SMTP_HOST` | SMTP | Хост SMTP-сервера | `mailpit` для тестов |
| `SMTP_PORT` | SMTP | Порт | `465` (SSL) или `1025` (Mailpit) |
| `SMTP_USE_SSL` | SMTP | TLS/SSL | `false` для Mailpit |
| `SMTP_SENDER_EMAIL` | SMTP | От кого |  |
| `SMTP_SENDER_PASSWORD` | SMTP | Пароль |  |
| `RUSENDER_API_KEY` | RuSender | Общий API-ключ доступа для отправки | Один ключ на приложение; ID ключа отправки хранится в подключении |
| `RUSENDER_API_BASE_URL` | RuSender | Базовый URL API | `https://api.rusender.ru/api/v1` |
| `RUSENDER_SENDER_NAME` | RuSender | Имя отправителя |  |
| `RUSENDER_SENDER_EMAIL` | RuSender | Email отправителя |  |
| `RUSENDER_WEBHOOK_TOKEN` | RuSender | Токен webhook (путь `/api/webhooks/rusender/{token}`) | Если не задан → 503 |
| `RUSENDER_WEBHOOK_SECRET` | RuSender | Секрет для подписи (не используется при token auth) |  |
| `MAILOPOST_API_TOKEN` | MailoPost | API-токен для отправки |  |
| `MAILOPOST_API_BASE_URL` | MailoPost | Базовый URL | `https://api.mailopost.ru/v1` |
| `MAILOPOST_SENDER_NAME` | MailoPost | Имя отправителя |  |
| `MAILOPOST_SENDER_EMAIL` | MailoPost | Email отправителя |  |
| `MAILOPOST_WEBHOOK_TOKEN` | MailoPost | Токен webhook (путь `/api/webhooks/mailopost/{token}`) |  |
| `UNISENDER_API_KEY` | UniSender Go / Classic | API-ключ |  |
| `UNISENDER_API_BASE_URL` | UniSender Go | `https://goapi.unisender.ru/...` → Go API; иначе Classic |  |
| `UNISENDER_SENDER_NAME` | UniSender | Имя отправителя |  |
| `UNISENDER_SENDER_EMAIL` | UniSender | Email отправителя |  |
| `UNISENDER_LIST_ID` | UniSender Classic | ID списка рассылки |  |
| `UNISENDER_WEBHOOK_TOKEN` | UniSender Go | Токен webhook |  |
| `UNISENDER_WEBHOOK_SECRET` | UniSender Go | Секрет (не используется при token auth) |  |
| `PUBLIC_BASE_URL` | Все | Публичный URL приложения для consent link и webhook | Обязателен для Level 2+ |
| `WEBHOOK_MAX_BODY_BYTES` | Все | Макс. размер тела webhook (default: 256 KB) |  |

### 4.2. Как выбирается transport

Функция `_resolve_transport(transport)` в `sender_agent.py` (строка ~1417):

```
1. Если в вызове явно передан transport и он в {"unisender","smtp","rusender","mailopost"} → использовать его.
2. Иначе → settings.sender_transport (env SENDER_TRANSPORT).
3. Если не распознан → fallback "smtp".

UniSender Classic vs Go:
  - transport="unisender" + UNISENDER_API_BASE_URL содержит "goapi.unisender.ru" → UniSender Go API.
  - transport="unisender" + другой base_url → UniSender Classic API.
```

### 4.3. Как сохраняется `provider_message_id`

После успешного вызова API провайдера функция `_append_sent_mail_log()` записывает в поток `job_events.sent_mail_log` объект с полями:

| Провайдер | Поле в ответе API | Куда сохраняется в log |
|---|---|---|
| RuSender | `response.task_id` | `sent_mail_log[n].provider.task_id` и `sent_mail_log[n].provider_message_id` |
| MailoPost | `response.message_id` | `sent_mail_log[n].provider.message_id` и `sent_mail_log[n].provider_message_id` |
| UniSender Go | `response.job_id` | `sent_mail_log[n].provider.job_id` / `provider_message_id` |
| UniSender Classic | `response.result.id` | `sent_mail_log[n].provider_message_id` |
| SMTP | — | `provider_message_id` пустой |

### 4.4. Как webhook сопоставляется с job / recipient

| Провайдер | Механизм сопоставления | Индекс | Fallback |
|---|---|---|---|
| **RuSender** | `event.task_id` → lookup по всем `sent_mail_log` → находит `job_id` | `_load_task_job_index()` строит map `task_id → {job_id, row_id, recipient}` | Несопоставленное → `tmp/rusender_events_unmatched.jsonl` |
| **MailoPost** | `event.message_id` → lookup по всем `sent_mail_log` | `_load_message_job_index()` строит map `message_id → {job_id, ...}` | Несопоставленное → `tmp/mailopost_events_unmatched.jsonl` |
| **UniSender Go** | `event.metadata.app_job_id` или `event.metadata.job_id` / `mailing_agent_job_id` | Из `global_metadata` в запросе отправки | Несопоставленное → пропускается |
| **UniSender Classic** | polling `checkEmail` по `provider_message_id` из `sent_mail_log` | Прямой lookup | Нет webhook — только polling |

### 4.5. Готовые тестовые endpoint-ы

Для имитации webhook без реального провайдера можно вручную слать POST:

```
POST /api/webhooks/rusender/{RUSENDER_WEBHOOK_TOKEN}
POST /api/webhooks/mailopost/{MAILOPOST_WEBHOOK_TOKEN}
POST /api/webhooks/unisender-go/{UNISENDER_WEBHOOK_TOKEN}
```

Для запуска отправки:
```
POST /api/sender/run   {"job_id":"...", "send_mode":"materials"}
GET  /api/sender/status?job_id=...
```

Нет специального «test send» endpoint — используется обычный run с тестовым job и allowlist-адресами.

### 4.6. Как поднять публичный URL для webhook (Level 2+)

Приложение доступно по `PUBLIC_BASE_URL`. Варианты для staging/тестов:

| Вариант | Подход | Переменные |
|---|---|---|
| **Production-like staging** | Сервер с nginx + SSL cert; app доступен напрямую | `PUBLIC_BASE_URL=https://staging.example.com` |
| **Cloudflare Tunnel** | `cloudflared tunnel --url http://localhost:9806` → постоянный URL в Cloudflare Zero Trust | `PUBLIC_BASE_URL=https://<tunnel>.trycloudflare.com` |
| **ngrok** | `ngrok http 9806` → временный URL (меняется при перезапуске) | `PUBLIC_BASE_URL=https://<id>.ngrok.io` |
| **SSH reverse tunnel** | `ssh -R 80:localhost:9806 serveo.net` | `PUBLIC_BASE_URL=https://<id>.serveo.net` |

> **Не внедрять** дополнительный тоннельный сервис в код приложения. Webhook URL настраивается только через `PUBLIC_BASE_URL` и в личном кабинете провайдера.

---

## 5. Основные external test-cases

### Сводная таблица

| ID | Провайдер | Сценарий | Уровень | Требует публичный URL | Требует mailbox |
|---|---|---|---|---|---|
| EXT-SEND-01 | RuSender | Real send + provider_message_id | L1 | Нет | Нет |
| EXT-SEND-02 | MailoPost | Real send + provider_message_id | L1 | Нет | Нет |
| EXT-SEND-03 | UniSender Go | Real send + provider_message_id | L1 | Нет | Нет |
| EXT-SEND-04 | UniSender Classic | Real send + polling | L1 | Нет | Нет |
| EXT-WEBHOOK-01 | RuSender | Реальный delivered webhook | L2 | **Да** | Нет |
| EXT-WEBHOOK-02 | MailoPost | Реальный delivered webhook | L2 | **Да** | Нет |
| EXT-WEBHOOK-03 | UniSender Go | Реальный delivered webhook | L2 | **Да** | Нет |
| EXT-OPEN-01 | RuSender | Open tracking через mailbox | L2+L3 | **Да** | **Да** |
| EXT-CLICK-01 | RuSender | Click tracking через mailbox | L2+L3 | **Да** | **Да** |
| EXT-CONSENT-01 | Любой | Consent flow через реальное письмо | L3 | **Да** | **Да** |
| EXT-FOLLOWUP-01 | Любой | Досылка КП после consent | L3 | **Да** | **Да** |
| EXT-BOUNCE-01 | RuSender | Hard bounce (sandbox address) | L2 | **Да** | Нет |
| EXT-BOUNCE-02 | MailoPost | Hard bounce (sandbox address) | L2 | **Да** | Нет |
| EXT-UNSUB-01 | RuSender | Unsubscribe event | L2 | **Да** | Нет |
| EXT-SPAM-01 | Любой | Spam/complaint (sandbox only) | L2 | **Да** | Нет |
| EXT-IDEM-01 | RuSender | Повторный реальный webhook | L2 | **Да** | Нет |
| EXT-IDEM-02 | MailoPost | Повторный реальный webhook | L2 | **Да** | Нет |
| EXT-RECON-01 | Все | Сверка provider API ↔ app ↔ mailbox | L4 | **Да** | **Да** |

---

### EXT-SEND-01 — RuSender real send

**Цель:** Убедиться, что реальная отправка через RuSender сохраняет `task_id` как `provider_message_id`.

**Предусловия:**
- `SENDER_TRANSPORT=rusender`
- `RUSENDER_API_KEY` настроен
- `RUSENDER_SENDER_EMAIL` — верифицированный домен RuSender
- Получатель — тестовый email из нашего allowlist

**Шаги:**
1. `POST /api/sender/run` `{"job_id":"<test_job>","send_mode":"materials"}`
2. Опрашивать `GET /api/sender/status?job_id=<test_job>` до `status=completed`
3. `SELECT payload FROM job_events WHERE stream='sent_mail_log' AND job_id='<test_job>'`
4. Проверить RuSender dashboard / API: письмо принято
5. `GET /api/sender/campaigns?job_id=<test_job>`
6. `GET /api/sender/recipients?job_id=<test_job>`

**Ожидаемый результат (провайдер):** RuSender dashboard → письмо в статусе accepted/sent.

**Ожидаемый результат (приложение):**
- `sent_mail_log[0].transport = "rusender"`
- `sent_mail_log[0].provider_message_id` — непустая строка, совпадает с `task_id` в RuSender dashboard
- `campaigns[0].sent = 1`
- `recipients[0].manager_status.key = "pending"` (webhook ещё не пришёл)

**Где сверять:** PostgreSQL `job_events`, `/api/sender/campaigns`, `/api/sender/recipients`, RuSender dashboard.

---

### EXT-SEND-02 — MailoPost real send

**Аналогично EXT-SEND-01 для MailoPost.**

**Предусловия:** `SENDER_TRANSPORT=mailopost`, `MAILOPOST_API_TOKEN`, `MAILOPOST_SENDER_EMAIL`.

**Ключевые отличия:**
- API path: `POST https://api.mailopost.ru/v1/email/messages`
- Идентификатор в ответе: `message_id`
- `sent_mail_log[0].provider_message_id` = значение `message_id` из ответа MailoPost

---

### EXT-SEND-03 — UniSender Go real send

**Аналогично EXT-SEND-01 для UniSender Go.**

**Предусловия:** `SENDER_TRANSPORT=unisender`, `UNISENDER_API_BASE_URL=https://goapi.unisender.ru/ru/transactional/api/v1`, `UNISENDER_API_KEY`.

**Ключевые отличия:**
- `global_metadata.app_job_id = job_id` — передаётся при отправке
- Идентификатор в ответе: `job_id`
- Webhook сопоставляется через `metadata.app_job_id`

---

### EXT-SEND-04 — UniSender Classic real send + polling

**Предусловия:** `SENDER_TRANSPORT=unisender`, `UNISENDER_API_BASE_URL` указывает на Classic API (не `goapi`).

**Шаги:**
1. Отправить через Classic API
2. Из `sent_mail_log` взять `provider_message_id`
3. Дождаться background polling (sender_report.py)
4. `GET /api/sender/recipients?job_id=<test_job>`

**Ожидаемый результат:** статус обновится из `ok_delivered` → `delivered` через polling. Нет webhook — только poll через `checkEmail`.

---

### EXT-WEBHOOK-01 — RuSender delivered webhook (реальный)

**Цель:** Убедиться, что реальный webhook от RuSender принимается и сопоставляется с нашим job.

**Предусловия:**
- Выполнен EXT-SEND-01 (есть `provider_message_id` = `task_id`)
- Приложение доступно по публичному URL (`PUBLIC_BASE_URL`)
- В кабинете RuSender настроен webhook URL: `{PUBLIC_BASE_URL}/api/webhooks/rusender/{RUSENDER_WEBHOOK_TOKEN}`

**Шаги:**
1. RuSender доставляет письмо → вызывает webhook с событием `external_mail.delivered`
2. Подождать 30–120 сек
3. Проверить `{job}/state/rusender_events.jsonl` — появилась запись с `event_type=external_mail.delivered`, `task_id=<provider_message_id>`, `provider_status=delivered`
4. `GET /api/sender/recipients?job_id=<test_job>` — проверить статус
5. `GET /api/sender/manager-dashboard?job_id=<test_job>` — проверить KPI

**Ожидаемый результат (провайдер):** RuSender dashboard → письмо в статусе `delivered`.

**Ожидаемый результат (приложение):**
- `rusender_events.jsonl` содержит запись с `job_id=<test_job>`
- `recipients[0].manager_status.key = "delivered"`
- `dashboard.summary.delivered = 1`
- `dashboard.rates.delivery_rate > 0`
- `campaigns[0].delivered = 1`

**Где сверять:** `{job}/state/rusender_events.jsonl`, `/api/sender/recipients`, `/api/sender/manager-dashboard`, RuSender dashboard/API.

---

### EXT-WEBHOOK-02 — MailoPost delivered webhook (реальный)

**Аналогично EXT-WEBHOOK-01 для MailoPost.**

**Ключевые отличия:**
- Webhook URL: `{PUBLIC_BASE_URL}/api/webhooks/mailopost/{MAILOPOST_WEBHOOK_TOKEN}`
- Сопоставление через `event.message_id`
- JSONL: `mailopost_events.jsonl`
- Event field: `"event":"delivered"`

---

### EXT-WEBHOOK-03 — UniSender Go delivered webhook (реальный)

**Аналогично EXT-WEBHOOK-01 для UniSender Go.**

**Ключевые отличия:**
- Webhook URL: `{PUBLIC_BASE_URL}/api/webhooks/unisender-go/{UNISENDER_WEBHOOK_TOKEN}`
- Сопоставление через `event.metadata.app_job_id`
- JSONL: `unisender_go_events.jsonl`
- Event field: `"event_name":"delivered"`

---

### EXT-OPEN-01 — Open tracking через реальный mailbox

**Цель:** Убедиться, что открытие письма в реальном клиенте генерирует open event у провайдера, который затем приходит в наш webhook.

**Предусловия:**
- Выполнен EXT-WEBHOOK-01 (письмо доставлено через RuSender)
- Провайдер поддерживает open-tracking (вставляет tracking pixel)
- Тестовый mailbox доступен (IMAP или API)
- В почтовом клиенте/webmail разрешена загрузка изображений (иначе pixel не сработает)

**Шаги:**
1. Открыть тестовый mailbox
2. Найти письмо по subject + to
3. Открыть письмо, убедиться, что изображения загружены (tracking pixel)
4. Подождать 30–120 сек → провайдер отправляет webhook `external_mail.open`
5. Проверить `rusender_events.jsonl` — новая запись с `event_type=external_mail.open`
6. `GET /api/sender/recipients?job_id=<test_job>`
7. `GET /api/sender/manager-dashboard?job_id=<test_job>`

**Ожидаемый результат (провайдер):** RuSender dashboard → open count = 1.

**Ожидаемый результат (приложение):**
- `recipients[0].manager_status.key = "opened"`
- `dashboard.summary.opened = 1`
- `dashboard.rates.open_rate > 0`

**Условие для open tracking:** письмо должно быть открыто в HTML-режиме с загрузкой картинок. Текстовые клиенты не генерируют open event.

---

### EXT-CLICK-01 — Click tracking через реальный mailbox

**Цель:** Убедиться, что клик по ссылке в письме генерирует click event.

**Предусловия:**
- Доставлено письмо через RuSender (EXT-WEBHOOK-01)
- Провайдер поддерживает click-tracking (оборачивает ссылки)
- Тестовый mailbox доступен

**Шаги:**
1. Открыть письмо в тестовом mailbox
2. Кликнуть по любой ссылке в письме
3. Подождать 30–60 сек → провайдер отправляет `external_mail.click`
4. Проверить `rusender_events.jsonl` — запись с `event_type=external_mail.click`, `url` заполнен
5. `GET /api/sender/recipients?job_id=<test_job>`
6. `GET /api/sender/manager-dashboard?job_id=<test_job>`

**Ожидаемый результат (провайдер):** click count = 1 в dashboard.

**Ожидаемый результат (приложение):**
- `recipients[0].manager_status.key = "clicked"`
- `recipients[0].interest.key = "high"`
- `dashboard.work_lists.interested` содержит организацию
- `dashboard.rates.ctr > 0`

**Known limitation:** клики через наш собственный прокси не отслеживаются (нет click proxy). Считаются только провайдерские click events.

---

### EXT-CONSENT-01 — Consent flow через реальное письмо

**Цель:** Полный E2E сценарий согласия через реальное письмо.

**Предусловия:**
- `SENDER_TRANSPORT=rusender` (или другой провайдер)
- `PUBLIC_BASE_URL` настроен и доступен
- Тестовый mailbox доступен для чтения

**Шаги:**
1. `POST /api/sender/run` `{"job_id":"<test_job>","send_mode":"consent_request"}`
2. Дождаться `status=completed`
3. Проверить `consents.json` — запись с `status=request_sent`
4. Проверить `sent_mail_log` — запись с `send_mode=consent_request`
5. Дождаться письма в тестовом mailbox (30–120 сек)
6. Найти письмо по subject (содержит "согласие")
7. Извлечь из HTML ссылку `/consent/confirm/{token}`
8. `GET {PUBLIC_BASE_URL}/consent/request/{token}` → убедиться HTTP 200, страница показывает preview
9. `POST {PUBLIC_BASE_URL}/consent/confirm/{token}` (или открыть URL в браузере и подтвердить)
10. Проверить `consents.json` — `status=confirmed`, `confirmed_at` заполнен, `ip` заполнен
11. `GET /api/sender/consents?job_id=<test_job>` — `summary.confirmed = 1`

**Ожидаемый результат (провайдер):** письмо отправлено, delivered (через webhook).

**Ожидаемый результат (приложение):**
- `consents.json[0].status = "confirmed"`
- `consents.json[0].confirmed_at` — непустая дата
- `consents.json[0].ip` — IP тестовой машины
- `consents.json[0].user_agent` — непустая строка
- `consents.json[0].materials_status = "queued"`
- `/api/sender/consents` → `summary.confirmed = 1`

**Ожидаемый результат (mailbox):** письмо реально пришло, ссылка в HTML рабочая.

---

### EXT-FOLLOWUP-01 — Досылка КП после consent

**Цель:** Убедиться, что после согласия реальная досылка КП уходит через провайдера и приходит в mailbox.

**Предусловия:** Выполнен EXT-CONSENT-01.

**Шаги:**
1. После подтверждения согласия (EXT-CONSENT-01 шаг 9) подождать до 30 сек
2. Проверить `consents.json` — `materials_status=sent`
3. Проверить `sent_mail_log` — новая запись с `send_mode=materials`, тот же `row_id`
4. Проверить провайдерский dashboard — вторая отправка
5. Дождаться письма в mailbox (30–120 сек)
6. Найти письмо по subject (КП / materials)
7. Проверить, что в mailbox именно PDF-вложение
8. `GET /api/sender/manager-dashboard?job_id=<test_job>` — `summary.materials_sent = 1`

**Ожидаемый результат (провайдер):** 2 отправки в dashboard — consent_request + materials.

**Ожидаемый результат (приложение):**
- `consents.json[0].materials_status = "sent"`
- `consents.json[0].materials_sent_at` заполнен
- `sent_mail_log` содержит 2 записи для `row_id` — consent_request и materials
- `dashboard.summary.materials_sent = 1`

**Ожидаемый результат (mailbox):** второе письмо пришло, содержит PDF-вложение.

---

### EXT-BOUNCE-01 — Hard bounce (безопасный тестовый адрес)

**Цель:** Убедиться, что hard bounce от провайдера корректно отражается в статистике.

**Предусловия:**
- Использовать безопасный bounce test address (проверить sandbox документацию провайдера):
  - RuSender: уточнить в документации — специальный bounce-тест адрес
  - MailoPost: уточнить в документации
  - Альтернатива: несуществующий домен вроде `test@nonexistent-domain-12345.invalid`
- Webhook настроен (Level 2)

**Шаги:**
1. Отправить письмо на bounce-тестовый адрес
2. Дождаться webhook `external_mail.hard_bounced` (может занять 1–10 мин)
3. Проверить `{job}/state/rusender_events.jsonl` — запись с `provider_status=hard_bounced`
4. `GET /api/sender/recipients?job_id=<test_job>`
5. `GET /api/sender/email-problems?job_id=<test_job>`

**Ожидаемый результат (провайдер):** bounce зафиксирован в dashboard.

**Ожидаемый результат (приложение):**
- `recipients[0].manager_status.key = "email_broken"`
- `recipients[0].bounce_reason` содержит причину (email_not_exists / inactive)
- `email-problems` — получатель присутствует в списке hard bounce
- `email-problems.summary.hard_bounce = 1`

> **Безопасность:** не использовать реальные адреса клиентов. Если провайдер не даёт sandbox bounce — пометить тест как **«manual / provider sandbox only»**.

---

### EXT-BOUNCE-02 — Soft bounce

**Аналогично EXT-BOUNCE-01, если провайдер поддерживает безопасную генерацию soft bounce.**

**Ожидаемый результат:**
- `recipients[0].manager_status.key = "soft_bounce"`
- `email-problems.summary.soft_bounce = 1`

> Если безопасный механизм soft bounce у провайдера отсутствует — пометить как **«manual / provider sandbox only»**.

---

### EXT-UNSUB-01 — Unsubscribe event

**Цель:** Убедиться, что unsubscribe от провайдера корректно отражается в статистике.

**Предусловия:** Отправить письмо на тестовый адрес. Использовать только тестовые адреса.

**Шаги:**
1. Отправить письмо на тестовый адрес (EXT-SEND-01)
2. Выполнить отписку через provider test link / unsubscribe URL в письме (только тестовый адрес)
3. Дождаться webhook `external_mail.unsubscribe`
4. Проверить `rusender_events.jsonl`
5. `GET /api/sender/recipients?job_id=<test_job>`

**Ожидаемый результат (провайдер):** адрес в статусе unsubscribed.

**Ожидаемый результат (приложение):**
- `recipients[0].manager_status.key = "unsubscribed"`
- `recipients[0].recommended_action.key = "do_not_contact"`

**Known gap (зафиксировать):** система сейчас **не блокирует** повторную отправку на unsubscribed/spam-адреса — нет системной блокировки в `sender_agent`. Это подтверждается тестом H-02 в `STATISTICS_TEST_PLAN.md`. Фиксировать как дефект при обнаружении.

---

### EXT-SPAM-01 — Spam / complaint

**Цель:** Убедиться, что spam complaint от провайдера корректно отражается в статистике.

> **ОГРАНИЧЕНИЕ:** Не создавать реальную жалобу на спам с обычного mailbox без необходимости. Использовать только если провайдер предоставляет sandbox/test complaint event.

**Шаги (только при наличии sandbox у провайдера):**
1. Использовать sandbox механизм провайдера для генерации complaint event
2. Дождаться webhook `external_mail.complaint`
3. Проверить `rusender_events.jsonl`
4. `GET /api/sender/recipients?job_id=<test_job>`

**Ожидаемый результат (при sandbox):**
- `recipients[0].manager_status.key = "spam"`
- `email-problems` содержит получателя с `bounce_reason_label = "Блокировка как спам"`

**Если sandbox отсутствует:** тест помечается как **«manual / provider sandbox only»** и не выполняется автоматически.

---

### EXT-IDEM-01 — Повторный реальный webhook от RuSender

**Цель:** Убедиться, что идемпотентность работает при реальных дублях от провайдера.

**Шаги:**
1. Дождаться реального webhook delivered от RuSender (EXT-WEBHOOK-01)
2. Скопировать точный payload из `rusender_events.jsonl`
3. Повторно `POST /api/webhooks/rusender/{RUSENDER_WEBHOOK_TOKEN}` с тем же payload
4. Подождать 2 сек
5. Проверить `rusender_events.jsonl` — количество записей с данным `event_key`
6. `GET /api/sender/manager-dashboard?job_id=<test_job>` — KPI не изменились

**Ожидаемый результат:**
- В `rusender_events.jsonl` ровно одна запись с данным `event_key`
- `dashboard.summary.delivered` не увеличился при повторном webhook
- Ответ на второй POST содержит `{"duplicates":1}`

---

### EXT-IDEM-02 — Повторный реальный webhook от MailoPost

**Аналогично EXT-IDEM-01 для MailoPost.** Проверить `mailopost_events.jsonl`.

---

### EXT-RECON-01 — Полная сверка provider ↔ app ↔ mailbox

**Цель:** Убедиться, что все четыре источника статистики показывают одинаковые цифры.

**Предусловия:** Выполнена серия: 5 sent → дождаться webhook delivered для 4 → open для 2 → click для 1 → hard bounce для 1.

**Шаги:**
1. Получить статистику из провайдерского API / dashboard:
   - Sent: N
   - Delivered: N-1
   - Opened: 2
   - Clicked: 1
   - Bounced: 1
2. `GET /api/sender/manager-dashboard?job_id=<test_job>` — извлечь `summary`
3. `GET /api/sender/campaigns?job_id=<test_job>` — извлечь rates
4. Посчитать записи в `sent_mail_log`: `SELECT COUNT(*) FROM job_events WHERE stream='sent_mail_log' AND job_id='<test_job>'`
5. Посчитать записи в `rusender_events.jsonl` по статусу
6. Проверить тестовый mailbox — количество писем

**Заполнить сверочную таблицу:**

| Метрика | Провайдер | App DB | App JSONL | App API | Mailbox | Статус |
|---|---:|---:|---:|---:|---:|---|
| sent | 5 | 5 | — | 5 | 5 | ✅ / ❌ |
| delivered | 4 | — | 4 | 4 | 4 | ✅ / ❌ |
| opened | 2 | — | 2 | 2 | — | ✅ / ❌ |
| clicked | 1 | — | 1 | 1 | — | ✅ / ❌ |
| hard_bounce | 1 | — | 1 | 1 | — | ✅ / ❌ |

**Ожидаемый результат:** все источники совпадают с учётом webhook latency (≤5 мин).

**Любое устойчивое расхождение (>5 мин после события) фиксируется как дефект статистики.**

---

## 6. Структура отчёта тестового прогона

```markdown
# External Statistics Test Report

## Метаданные прогона
- Дата: YYYY-MM-DD HH:MM
- Job ID: <test_job>
- Провайдер: rusender / mailopost / unisender_go
- Transport: rusender
- PUBLIC_BASE_URL: https://...
- Тестовый mailbox: test@example.com

## Summary

| Метрика | App API | Provider | Mailbox | Статус |
|---|---:|---:|---:|---|
| sent | 5 | 5 | 5 | ✅ |
| delivered | 4 | 4 | 4 | ✅ |
| opened | 2 | 2 | — | ✅ |
| clicked | 1 | 1 | — | ✅ |
| hard_bounce | 1 | 1 | — | ✅ |
| materials_sent | 1 | 2* | 1 | ⚠️ расхождение |

## Provider Events

| Provider | Message ID | Event | Provider timestamp | App received timestamp | Delay sec | Status |
|---|---|---|---|---|---:|---|
| RuSender | task_abc123 | delivered | 2026-07-09T10:01:00Z | 2026-07-09T10:01:03Z | 3 | ✅ |
| RuSender | task_abc123 | opened | 2026-07-09T10:05:00Z | 2026-07-09T10:05:02Z | 2 | ✅ |

## Recipient Checks

| Email | Send mode | Provider status | App status | Mailbox received | Consent | Follow-up | Result |
|---|---|---|---|---|---|---|---|
| test@example.com | consent_request | delivered | delivered | ✅ | confirmed | sent | ✅ |
| test2@example.com | materials | hard_bounced | email_broken | ❌ | — | — | ✅ |

## Mismatches

| Тип | Ожидалось | Фактически | Источник | Severity |
|---|---|---|---|---|
| materials_sent count | provider=2, app=1 | расхождение на 1 | /api/sender/campaigns | HIGH |

## Known Gaps

- SMTP не предоставляет delivered/bounced без DSN/IMAP-обработчика.
- Нет custom tracking pixel — open events только от провайдера.
- Нет click proxy — click events только от провайдера.
- `clicked_after_consent` захардкожен = 0 в `build_consents_view`.
- Unsubscribe/spam не блокируют повторную отправку (нет системной блокировки).
- UniSender Classic — только polling, нет webhook, задержка статуса.
- История событий получателя (timeline) — только последний статус в UI.
```

---

## 7. Приоритеты реализации тестового агента

> Только план, не реализация.

### 7.1. Provider adapter layer

| Адаптер | Методы | Источники данных |
|---|---|---|
| `RuSenderAdapter` | `get_sent_count()`, `get_delivered_count()`, `get_events_by_task_id(task_id)` | RuSender REST API |
| `MailoPostAdapter` | `get_message_status(message_id)`, `get_campaign_stats()` | MailoPost REST API |
| `UniSenderGoAdapter` | `get_job_status(job_id)`, `get_email_events()` | UniSender Go Transactional API |
| `UniSenderClassicAdapter` | `check_email(message_ids)` | UniSender Classic `checkEmail` API |

### 7.2. Mailbox adapter

| Функция | Реализация |
|---|---|
| Подключение | IMAP (imaplib) или mailbox REST API (Mailtrap / mail.ru) |
| `find_message(subject, to, since)` | Поиск письма по теме и адресу |
| `get_html_body(message_id)` | Извлечение HTML-тела |
| `extract_links(html)` | Поиск ссылок `/consent/confirm/`, `/unsubscribe/` |
| `count_messages(since)` | Подсчёт писем за период |

### 7.3. App adapter

| Функция | Endpoint / метод |
|---|---|
| `login(username, password)` | `POST /api/auth/login` → bearer token |
| `run_sender(job_id, send_mode)` | `POST /api/sender/run` |
| `wait_for_completed(job_id, timeout)` | polling `GET /api/sender/status` |
| `get_dashboard(job_id)` | `GET /api/sender/manager-dashboard` |
| `get_campaigns(job_id)` | `GET /api/sender/campaigns` |
| `get_recipients(job_id)` | `GET /api/sender/recipients` |
| `get_consents(job_id)` | `GET /api/sender/consents` |
| `get_email_problems(job_id)` | `GET /api/sender/email-problems` |
| `get_sent_mail_log(job_id)` | SQL: `SELECT payload FROM job_events WHERE stream='sent_mail_log'` |
| `get_events_jsonl(job_id, provider)` | Файл `{job}/state/{provider}_events.jsonl` через object_store |

### 7.4. Reconciliation engine

```
1. Собрать данные из всех источников (provider_adapter, mailbox_adapter, app_adapter).
2. Нормализовать к общей схеме: {email, event_type, timestamp, source}.
3. JOIN по email + event_type (с tolerance на timestamp ±5 мин).
4. Выявить расхождения: события в провайдере, которых нет в app и наоборот.
5. Вернуть список Mismatch(expected, actual, source, severity).
```

### 7.5. Report writer

| Формат | Назначение |
|---|---|
| JSON | Machine-readable для CI |
| Markdown | Читаемый отчёт для коммита / PR comment |
| HTML | Интерактивный отчёт с таблицами и цветовой подсветкой |

---

## 8. Финальный вывод

### Что реально поддерживается сейчас

| Система | Статус | Комментарий |
|---|---|---|
| RuSender | ✅ Полная поддержка | Send + webhook (delivered/open/click/bounce/unsubscribe/complaint) |
| MailoPost | ✅ Полная поддержка | Send + webhook (delivered/open/click/bounce/unsubscribe/complaint) |
| UniSender Go | ✅ Полная поддержка | Send + webhook (все основные события) |
| UniSender Classic | ⚠️ Частичная | Send + polling (нет webhook; задержка статуса) |
| SMTP | ⚠️ Ограниченная | Send accepted; нет delivered/bounce без DSN; нет open/click |

### Что можно запускать сразу (без публичного URL)

- EXT-SEND-01, EXT-SEND-02, EXT-SEND-03, EXT-SEND-04 — только Level 1 (отправка + provider_message_id)
- Ручная имитация webhook через `POST /api/webhooks/*/{token}` (уже в `STATISTICS_TEST_PLAN.md`)

### Что требует публичного URL для webhook (Level 2+)

- Все EXT-WEBHOOK-* тесты
- EXT-OPEN-01, EXT-CLICK-01
- EXT-BOUNCE-01, EXT-BOUNCE-02, EXT-UNSUB-01, EXT-SPAM-01
- EXT-IDEM-01, EXT-IDEM-02
- EXT-RECON-01
- Решение: staging-сервер или Cloudflare Tunnel / ngrok (`PUBLIC_BASE_URL` + webhook в кабинете провайдера)

### Что требует доступа к provider API/dashboard

- EXT-RECON-01 — сверка статистики
- EXT-SEND-01..04 — проверка принятия письма провайдером
- Все EXT-WEBHOOK-* — проверка статуса у провайдера

### Что требует тестового mailbox (Level 3)

- EXT-OPEN-01, EXT-CLICK-01
- EXT-CONSENT-01, EXT-FOLLOWUP-01
- EXT-RECON-01

### Какие события нельзя безопасно проверить без sandbox провайдера

| Событие | Статус |
|---|---|
| Hard bounce | ⚠️ Только если провайдер даёт безопасный bounce test address |
| Soft bounce | ⚠️ Manual / provider sandbox only |
| Spam / complaint | ❌ Manual / provider sandbox only — не создавать реальную жалобу |
| Unsubscribe (тестовый адрес) | ✅ Безопасно если только тестовые адреса |

### Known gaps (фиксировать как технический долг)

| # | Gap | Severity |
|---|---|---|
| 1 | `clicked_after_consent` захардкожен = 0 | Medium |
| 2 | Нет системной блокировки повторной отправки после unsubscribe/spam | High |
| 3 | SMTP — нет bounce/delivered/open/click tracking | Medium (ограничение транспорта) |
| 4 | История событий получателя (timeline) — только последний статус | Low |
| 5 | Согласия хранятся в файлах, не в PostgreSQL — нет транзакционной гарантии | Medium |
| 6 | UniSender Classic — только polling, задержка статуса | Low |
| 7 | Нет click proxy — клики считаются только если провайдер поддерживает | Medium |
