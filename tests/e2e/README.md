# E2E send matrix (real RuSender delivery)

Сквозной тест: для каждого вида работ, каждого формирования и (где есть КП) каждого шаблона КП
генерирует документы через HTTP API и реально отправляет письма через RuSender.

## Матрица

- **5 видов работ**: `mngp_settlements`, `mngp_districts`, `stp_mo`, `random_forest`, `territorial_zone_boundaries`
- **3 формирования** (`document_mode`): `kp`, `both`, `contract`
- **3 шаблона КП** (для `kp` и `both`): `kp_1.docx`, `kp_2.docx`, `kp_3.docx`
- **4 вида отправки на job**: `send_mode` × `recipient_strategy`
  - `consent_request` / `materials`
  - `all` / `primary_then_fallback`
- Транспорт: только **RuSender** (`transport=rusender`)

Итого: **35 job** генерации, **140** запусков отправки, сотни писем на адреса из `recipients.xlsx`.

## Фикстуры

Файлы лежат в `tests/e2e/fixtures/`:

- `recipients.xlsx`
- `mail_template.txt`
- `kp_1.docx`, `kp_2.docx`, `kp_3.docx`
- `agreement.docx`

Переопределить каталог: `E2E_FIXTURES_DIR=/path/to/fixtures`.

## Подготовка окружения

1. Скопировать шаблон переменных:

```bash
cp .env.e2e.example .env.docker
```

2. Заполнить реальные значения:

- `RUSENDER_API_KEY`
- `RUSENDER_SENDER_EMAIL` (верифицированный отправитель)
- `RUSENDER_SENDER_NAME`
- `RUSENDER_WEBHOOK_SECRET`
- `APP_USERNAME` / `APP_PASSWORD`

3. Поднять стек:

```bash
docker compose up -d
```

Нужны: `postgres`, `minio`, `gotenberg`, `redis`, `app`.

4. Проверить доступность:

- `GET http://localhost:9806/api/webhooks/rusender`
- логин `POST /api/auth/login`

## Запуск

### Быстрый параллельный прогон (~30–60 мин)

Рекомендуемый способ для полной матрицы. Скрипт поднимает лимиты worker-процессов,
очищает отчёты и запускает runner с параллелизмом по generation job:

```powershell
# Windows (из корня репозитория)
.\scripts\run-e2e-fast.ps1
```

Скрипт выставляет:

- `DOCUMENTS_WORKER_MAX_PROCESSES=8`
- `SENDER_WORKER_MAX_PROCESSES=8`
- `USER_WORKER_MAX_PROCESSES_PER_TASK=8`
- `USER_INPROCESS_MAX_TASKS=8`
- `E2E_PARALLEL_JOBS=6`
- `E2E_SEND_PAUSE_SECONDS=1`

**Важно:** `USER_WORKER_MAX_PROCESSES_PER_TASK` должен быть ≥ `E2E_PARALLEL_JOBS`,
иначе сервер отклонит второй concurrent documents/sender worker для того же пользователя.

### Полная матрица (внутри контейнера app)

```bash
RUN_REAL_E2E=1 docker compose exec app .venv/bin/python -m tests.e2e.run_send_matrix
```

Или через скрипт с preflight-проверкой env:

```powershell
# Windows (из корня репозитория, после docker compose up -d)
.\scripts\run-e2e-matrix.ps1
```

```bash
# Linux/macOS
RUN_REAL_E2E=1 ./scripts/run-e2e-matrix.sh
```

### Чистый прогон (без resume)

```bash
docker compose exec app rm -f tests/e2e/out/e2e_report.json tests/e2e/out/e2e_state.json
find tmp/storage/jobs -path '*/state/.sender.run.lock' -delete
```

### Локально (если app запущен на хосте)

```bash
set RUN_REAL_E2E=1
set E2E_BASE_URL=http://localhost:9806
python -m tests.e2e.run_send_matrix
```

### Через unittest (тот же прогон, но с skip без флага)

```bash
RUN_REAL_E2E=1 python -m unittest tests.e2e.test_send_matrix_smoke
```

## Фильтры для отладки

Можно сузить матрицу переменными окружения:

- `E2E_FILTER_WORK_TYPE=stp_mo`
- `E2E_FILTER_DOCUMENT_MODE=both`
- `E2E_FILTER_KP_VARIANT=kp_2.docx`
- `E2E_FILTER_SEND_MODE=materials`
- `E2E_FILTER_RECIPIENT_STRATEGY=all`

## Параллелизм и ускорение

Runner параллелит **generation job** (разные `job_id`), но send-сценарии **внутри одного job**
остаются последовательными (consent_request перед materials).

Переменные:

- `E2E_PARALLEL_JOBS` — число одновременных generation job (по умолчанию `1`, для fast-run: `6`)
- `E2E_SEND_PAUSE_SECONDS` — пауза между send-сценариями (по умолчанию `10`, для fast-run: `1`)

Лимиты worker-процессов app (через env или `docker-compose.yml`):

- `DOCUMENTS_WORKER_MAX_PROCESSES`
- `SENDER_WORKER_MAX_PROCESSES`
- `USER_WORKER_MAX_PROCESSES_PER_TASK`
- `USER_INPROCESS_MAX_TASKS`

Пример ручного fast-run:

```bash
DOCUMENTS_WORKER_MAX_PROCESSES=8 SENDER_WORKER_MAX_PROCESSES=8 \
USER_WORKER_MAX_PROCESSES_PER_TASK=8 USER_INPROCESS_MAX_TASKS=8 \
docker compose up -d app

RUN_REAL_E2E=1 E2E_PARALLEL_JOBS=6 E2E_SEND_PAUSE_SECONDS=1 \
docker compose exec app .venv/bin/python -m tests.e2e.run_send_matrix
```

Smoke-тест параллельного пути (несколько минут):

```bash
RUN_REAL_E2E=1 E2E_FILTER_WORK_TYPE=mngp_settlements E2E_PARALLEL_JOBS=4 \
E2E_SEND_PAUSE_SECONDS=1 docker compose exec app .venv/bin/python -m tests.e2e.run_send_matrix
```

## Отчёты и resume

После прогона:

- `tests/e2e/out/e2e_report.json`
- `tests/e2e/out/e2e_report.csv`
- `tests/e2e/out/e2e_state.json` (кэш `job_id` по комбинациям генерации)

Повторный запуск пропускает сценарии со статусом `success` в отчёте (только aggregate-строки без `recipient`).

Пауза между отправками: `E2E_SEND_PAUSE_SECONDS` (по умолчанию 10).

Ожидаемое время полного прогона:

- **последовательно** (без `E2E_PARALLEL_JOBS`): **3–6 часов**
- **параллельно** (`E2E_PARALLEL_JOBS=6`, `run-e2e-fast.ps1`): **~30–60 минут**

## Изоляция сценариев

Каждый send-сценарий сбрасывает состояние job перед запуском:

- повторная загрузка `recipients.xlsx` (сброс `STATUS`)
- очистка `state/sender.json` и `.sender.run.lock`
- очистка `state/consents.json`

Для `materials` дополнительно выполняется синтетический `consent_request` → подтверждение токенов → сброс sender state с сохранением consent.

Критерии успеха: `error_rows == 0`, все 2 получателя из fixtures без `blocked_*` ошибок.

## Проверка писем в ящиках

Автоматически собираются:

- статусы `GET /api/sender/status`
- события `GET /api/sender/analytics?refresh=true`
- записи `sent_mail_log` (если runner запущен в venv/контейнере с доступом к БД)

Вручную проверьте ящики из `recipients.xlsx`:

- `ventilator394@gmail.com`
- `a.terehov@ciales.ru`

Для сценария `consent_request` тест сам подтверждает согласие через `GET /consent/confirm/{token}` и дожидается отправки материалов.

Для `materials` перед отправкой подтверждается согласие (если ещё не было).

## Особые случаи

- **`random_forest`**: используется загруженный DOCX-шаблон КП (`KP_GENERATION_ENGINE=template`). PDF-шаблон нужен только при загрузке `.pdf`.
- **`territorial_zone_boundaries`**: другая цена/раскладка КП — на отправку не влияет.
- **`EMAIL_DOP` пуст**: стратегии `all` и `primary_then_fallback` ведут себя одинаково (только основной email).

## Безопасность

- Без `RUN_REAL_E2E=1` прогон не стартует.
- E2E не входит в `python -m tests` (отдельный runner `tests.e2e.run_send_matrix`).
- SMTP реальная отправка отключена (`SMTP_ALLOW_REAL_SEND=0`).
