# Mailing Agent



Сервис для подготовки и отправки персональных коммерческих предложений и договоров по клиентской таблице.



Проект автоматизирует рабочий цикл рассылки:



1. подготовка шаблонов и параметров отправки;

2. загрузка или сбор таблицы клиентов;

3. генерация документов по шаблонам;

4. проверка текстов и безопасные исправления;

5. проверка адресов и вложений;

6. отправка писем и просмотр результата отправки.



## Основные модули



### Настройки



Пользователь загружает шаблоны письма, КП и договора, задает тему письма и параметры отправителя. Эти данные используются дальше во всем пайплайне.



### Таблица клиентов



Сервис принимает Excel-таблицу с клиентами или помогает собрать данные через парсер. После загрузки таблица проверяется и подготавливается для генерации документов.



### Документы



Модуль создает для каждого клиента папку с документами:



- коммерческое предложение;

- договор;

- PDF-версии документов;

- журнал исправлений, если были текстовые правки.



Проверка документов выполняется в фоне. Прогресс сохраняется в состоянии job, поэтому после перезапуска страницы пользователь может вернуться к текущей задаче.



### Отправка писем



Отправка проходит в два шага:



1. предварительная проверка адресов и вложений без реальной отправки;

2. подтверждение пользователем и реальная отправка.



Для отправки поддерживаются SMTP и RuSender API. Результаты сохраняются в статусах и отчетах текущей рабочей сессии.



Основной сценарий отправки сейчас двухэтапный: сначала получателю отправляется запрос согласия, после клика по ссылке сервис фиксирует согласие и может отправить выбранный пакет документов.



## Запуск через Docker Compose



Сервис работает **только** в Docker. Локальный `.venv` и прямой запуск `uvicorn` не поддерживаются.



### Режимы стека

| Режим | Команда | Compose | БД / проект | Назначение |
|-------|---------|---------|-------------|------------|
| **Prod-like** | `docker compose up -d --build` + `.env.docker` | `docker-compose.yml` | `mailing` (default project) | Деплой-подобный стек без Mailpit. Образ `mailing-agent:local` с bind-mount `./src` — не immutable image-only prod. |
| **Local UI (dev)** | `.\scripts\dev.ps1 start` | base + `docker-compose.dev.yml` | `mailing` / volume `pgdata` | Ручная работа с React UI на `:9806` + Mailpit `:8025` |
| **E2E** | `npm run e2e:*` / `.\scripts\e2e.ps1` + `.env.e2e` | base + `docker-compose.e2e.yml` | `mailing_e2e` / project `mailing-agent-e2e` | Playwright (порты по умолчанию `19806` / `18025`) |
| **Unit/integration** | `docker compose -p mailing-agent-test -f docker-compose.test.yml run --rm test` | `docker-compose.test.yml` | `mailing_test` / project `mailing-agent-test` | Python-тесты (Postgres + MinIO only; отдельный project, чтобы не трогать local) |

Опциональные профили на base compose: `migrate` / `verify`, `onlyoffice`, `gotenberg-ha`. На production профиль `onlyoffice` включается автоматически скриптами deploy/audit. В e2e — profile `playwright`.

Проверки: `GET /health` — liveness (БД); `GET /ready` — readiness (БД, Redis, MinIO, Gotenberg). Worker имеет свой Docker healthcheck (heartbeat + БД + Redis).



Docker Compose поднимает:

- `app` — FastAPI-приложение на Python;
- `worker` — фоновая очередь задач (обязателен рядом с `app`);
- `postgres` — PostgreSQL (строковые/state-данные, auth, клиенты, события);
- `minio` — S3-совместимое хранилище файлов (xlsx, docx, pdf, шаблоны);
- `redis` — кэш и progress streams парсера;
- `gotenberg` — внутренний сервис для DOCX → PDF (второй инстанс: profile `gotenberg-ha`).
- `onlyoffice` — редактор документов (profile `onlyoffice`).
- `mailpit` — только в dev/e2e overlays (локальный SMTP sink).

Данные приложения:
- **PostgreSQL** — users/sessions, состояние агентов, owner, consents, события (`*.jsonl`), строки клиентов;
- **MinIO (S3)** — бинарные файлы job (`input/`, `templates/`, `output/`, `consents/`, отчёты);
- **локальный `/app/tmp`** — рабочая папка для генерации документов (синхронизируется с S3 на границах upload/worker/finalize).



Gotenberg не публикуется наружу и доступен контейнеру приложения по адресу `http://gotenberg:3000`. PDF-конвертация настроена через Gotenberg.

OnlyOffice на production доступен только через Caddy по HTTPS-маршруту `https://offer.parresh.ru/onlyoffice`; прямой порт Document Server на хосте не публикуется. Приложение и Document Server используют общий `ONLYOFFICE_JWT_SECRET` из `.env.docker`. Закреплённый образ `9.4.0.1` зеркалируется workflow в GHCR, чтобы deploy не зависел от анонимного лимита Docker Hub.



Первый запуск:



```bash

cd /opt/mailing-agent

cp .env.docker.example .env.docker

nano .env.docker

```



В `.env.docker` обязательно задать реальные значения `APP_PASSWORD`, `PUBLIC_BASE_URL`, ключи LLM и настройки отправщика писем. `PUBLIC_BASE_URL` должен быть внешним адресом сервиса, потому что он используется в consent-ссылках из писем и webhook-сценариях.



Запуск:



```bash

docker compose up -d --build

```



Миграция существующих данных с диска в PostgreSQL и MinIO (первый деплой на сервере с историческими job-ами):



```bash

# Убедиться, что ./storage/jobs содержит исторические job-ы.
# Если новые job-ы уже созданы в ./tmp/storage/jobs, смержить их в storage:
# cp -rn ./tmp/storage/jobs/* ./storage/jobs/

docker compose --profile migrate run --rm migrate
docker compose --profile migrate run --rm verify
docker compose up -d
```



Скрипт `migrate` импортирует данные из `./storage` (через mount `/app/legacy/storage`) и из текущего `./tmp/storage/jobs`. Скрипт `verify` сравнивает количество записей в файлах и в PostgreSQL.



После запуска открыть (порт по умолчанию `9806`):



```text

http://localhost:9806/

```



Проверка:



```bash

docker compose ps

curl -sf http://localhost:9806/health
curl -sf http://localhost:9806/ready

docker compose logs -f app

docker compose logs -f worker

```

Локальный UI (с Mailpit): `.\scripts\dev.ps1 start` — ждёт `/health`, `/ready`, Mailpit и healthy worker.

The `worker` service executes durable PostgreSQL-backed tasks. Queued and
running tasks survive API restarts; expired leases are retried automatically.
The API service must not be deployed without at least one worker replica.



Production deploy (offer.parresh.ru):

### Автодеплой (push в `main`)

```text
push main
  ├─ unit          (frontend + test image)
  ├─ build-image   (push runtime :latest + :sha → GHCR)   ──┐
  └─ e2e-smoke     (pull :sha, no second runtime build)   │
                                                          ▼
                                               deploy-prod (restricted SSH)
                                                 exact main SHA only
                                                 deploy.sh + prod-audit
                                                 automatic rollback on failure
```

На `main` runtime-образ собирается **один раз** и переиспользуется e2e + deploy. Job `deploy-prod` в concurrency-группе `deploy-prod` с `cancel-in-progress: false` — второй push не убивает текущий SSH-деплой. Сервер принимает только полный SHA коммита из `origin/main`; устаревший queued deploy не может откатить уже работающую более новую версию.

**One-time setup:**

1. Создайте отдельную пару SSH-ключей ED25519.
2. На prod-хосте выполните от `root`: `bash scripts/provision-deploy-user.sh /path/to/key.pub`.
3. Добавьте приватный ключ в GitHub Actions secret `PROD_SSH_KEY` (repository secret либо secret окружения `production`).
4. На сервере должен быть настроен `docker login ghcr.io` (PAT с `read:packages`, если пакет private).
5. Добавьте в `/opt/mailing-agent/.env.docker` отдельный случайный `ONLYOFFICE_JWT_SECRET` длиной не менее 32 символов.
6. Для полностью автоматического деплоя не включайте required reviewers у GitHub Environment `production`.

Host, пользователь и проверенный ED25519 host key не являются секретами и зафиксированы в workflow/`.github/known_hosts`. Пользователь `deploy` не состоит в группе `docker` и не получает обычный SSH shell: его ключ может вызвать только `deploy <40-char-main-commit-sha>`. Root-owned wrapper сериализует деплои, фиксирует checkout на точном SHA, запускает health/audit gates и возвращает предыдущий image + checkout, если новая версия не проходит проверки. Длинный server-side deploy переживает обрыв SSH, пишет подробный лог в `/var/log/mailing-agent-deploy.log`, а SSH-сессия отправляет heartbeat каждые 30 секунд.

Первый push в `main` без `PROD_SSH_KEY` завершит `deploy-prod` понятной ошибкой. После настройки секрета выполните **Re-run failed jobs**.

### Ручной deploy

```bash
cd /opt/mailing-agent
chmod +x scripts/deploy.sh scripts/prod-audit.sh scripts/post-deploy-stats.sh

# Обычный путь: pull immutable образа из GHCR (~3–5 min)
MAILING_AGENT_IMAGE=ghcr.io/parallel-solutions/mailing-agent:<sha> ./scripts/deploy.sh --pull

# Для root-owned deploy wrapper: checkout уже зафиксирован на точном SHA
MAILING_AGENT_IMAGE=ghcr.io/parallel-solutions/mailing-agent:<sha> \
  ./scripts/deploy.sh --pull --skip-git-update

# Тот же путь с latest (если sha неизвестен)
MAILING_AGENT_IMAGE=ghcr.io/parallel-solutions/mailing-agent:latest ./scripts/deploy.sh --pull

# Без pull — только restart уже запущенных контейнеров
./scripts/deploy.sh --no-build

# После backfill sent_mail_log / gap в статистике
./scripts/deploy.sh --pull --post-deploy-stats

# Конкретная ветка/тег (git) + pull образа
./scripts/deploy.sh --ref release/companies-campaign-wizard-2026-07-22 --pull

# Emergency only: rebuild на сервере (часто упирается в Docker Hub rate limit)
./scripts/deploy.sh

# Ручной аудит (exit ≠ 0 = плохо)
./scripts/prod-audit.sh
```

`deploy.sh --pull` делает `docker pull` + `up --force-recreate` для `app`/`worker`, поднимает закреплённый OnlyOffice, сверяет Image ID, ждёт local (`:9806`), public health и публичный API редактора, затем гоняет [`scripts/prod-audit.sh`](scripts/prod-audit.sh) как gate. `--skip-git-update` разрешён для root-owned wrapper, который сам проверяет принадлежность SHA к `origin/main`. Overlay [`docker-compose.prod.yml`](docker-compose.prod.yml): `PUBLIC_BASE_URL`, JWT-защита редактора, без RuSender click-tracking, без bind-mount `./src`, MinIO только на `127.0.0.1`. Всегда поднимайте `app` и `worker` вместе.

### Server checklist (после первого деплоя / при сомнениях)

```bash
cd /opt/mailing-agent
docker compose --env-file .env.docker --profile onlyoffice -f docker-compose.yml -f docker-compose.prod.yml ps
# expect: app, worker, postgres, minio, redis, gotenberg, onlyoffice (minio-init exited OK)

docker ps --format '{{.Names}} {{.Image}}'
# no gotenberg-2 / mailpit / playwright / mailing-agent-e2e / mailing-agent-test

docker inspect mailing-agent-app-1 --format '{{.Config.Image}} {{.Image}}'
# must be ghcr.io/parallel-solutions/mailing-agent:<sha>

MAILING_AGENT_IMAGE=ghcr.io/parallel-solutions/mailing-agent:<sha> ./scripts/prod-audit.sh
# must exit 0
```

После смены `PUBLIC_BASE_URL` или webhook-токена может понадобиться resend кампаний — см. [`scripts/verify-production-links.ps1`](scripts/verify-production-links.ps1) и [`scripts/resend-chain-campaign.ps1`](scripts/resend-chain-campaign.ps1).



Тесты:

**QA tiers** (рекомендуемый порядок):

```powershell
.\scripts\qa.ps1 fast    # ~10 min: 6 backend + e2e smoke + campaign email
.\scripts\qa.ps1 gate    # ~20 min: frontend + full backend + e2e smoke (CI parity)
.\scripts\qa.ps1 full    # ~30 min: gate + e2e email (chromium)
```

**Unit/integration** (без реальной отправки, Postgres + MinIO):

```bash
docker compose -p mailing-agent-test -f docker-compose.test.yml run --rm test
```

Локально (из корня проекта, с активированным `.venv`):

```bash
python -m tests
```

**Playwright E2E** (React CampaignFlow + Mailpit, отдельный compose-проект `mailing-agent-e2e`):

```bash
cp .env.e2e.example .env.e2e   # не копировать в .env.docker
npm run e2e:up:fast            # warm stack, без rebuild (Python через mount src)
npm run e2e:up:build           # rebuild app+worker после frontend/Dockerfile
npm run e2e:test:smoke
npm run e2e:test:email         # chromium only
npm run e2e:down
```

Подробности: [`docs/testing/PLAYWRIGHT_DOCKER.md`](docs/testing/PLAYWRIGHT_DOCKER.md).

Остановка:



```bash

docker compose down

```



По умолчанию приложение публикуется на порту `9806` хоста. Если нужен другой порт, задайте в `.env.docker`, например:



```env

APP_PUBLIC_PORT=18080

```



Если на сервере уже заняты `80/tcp` и `443/tcp`, лучше оставить приложение на внутреннем порту `9806` и добавить отдельный поддомен через существующий nginx/reverse proxy.



Runtime-данные на хосте: `./logs` и `./tmp` (рабочая папка `/app/tmp`). PostgreSQL, MinIO и Redis хранятся в named Docker volumes (`pgdata`, `minio-data`, `redis-data`).



## Переменные окружения



Пример настроек лежит в `.env.docker.example`.



Перед запуском нужно создать `.env.docker` и заполнить реальные значения:



- `DATABASE_URL` — PostgreSQL DSN (по умолчанию `postgresql+psycopg://mailing:mailing@postgres:5432/mailing`);

- `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` — MinIO/S3 для файлов job;

- `REDIS_URL` — Redis для кэша парсера;

- `WORKSPACE_DIR` — локальная рабочая папка (`/app/tmp` в Docker);

- `APP_USERNAME`, `APP_PASSWORD` — admin fallback для входа; `APP_PASSWORD` обязателен, с пустым значением сервис не запустится;

- `APP_ALLOW_REGISTRATION` — открытая регистрация (`1`/`0`, по умолчанию `0` = только admin создаёт пользователей);

- `APP_USERS` — optional JSON map пользователей для multi-user режима, например `{"alice":{"password":"...","tenant_id":"team","role":"user"}}`; пользователи видят только свои jobs, admin видит все;

- `SENDER_WORKER_MAX_PROCESSES=1` — одновременно выполняется одна sender-задача; остальные встают в PostgreSQL-очередь;

- `SENDER_DOMAIN_LIMITS_JSON` — лимиты отправки на домены получателей за окно `SENDER_DOMAIN_LIMIT_WINDOW_SECONDS`;

- `PUBLIC_BASE_URL` — внешний URL сервиса для consent-ссылок и webhooks;

- `CONSENT_TOKEN_TTL_HOURS` — срок действия публичных consent-ссылок, по умолчанию 720 часов;

- `OPENAI_API_KEY`, `OPENAI_BASE_URL` — доступ к LLM-провайдеру;

- `UNISENDER_API_KEY`, `UNISENDER_API_BASE_URL` — доступ к UniSender/UniSender Go;

- `UNISENDER_SENDER_EMAIL`, `UNISENDER_SENDER_NAME` — отправитель писем;

- `SMTP_*` — резервные SMTP-настройки, если используются.

- `SMTPBZ_API_KEY` — API-ключ валидатора из панели SMTP.BZ; по умолчанию каждый адрес проверяется перед отправкой. `SMTPBZ_FAIL_OPEN=0` блокирует отправку, если проверка недоступна.

- `SENDER_DELAY_SECONDS` — фиксированная пауза между SMTP-письмами; `SENDER_DELAY_MIN_SECONDS`/`SENDER_DELAY_MAX_SECONDS` задают случайную паузу, например `179` и `247` для диапазона 2:59–4:07.



Файл `.env.docker` не должен попадать в git.



## Структура проекта



```text

main.py                         FastAPI-приложение и API-роуты

frontend/                       React UI (CampaignFlow), собирается в Docker

src/generator/generation/       генерация DOCX/PDF

src/generator/philologist/      проверка и исправление текстов

src/generator/delivery/         проверка адресов, отправка, отчеты

src/generator/orchestration/    общая логика пайплайна

src/parser_new/                 парсер и сбор данных

data/knowledge/                 правила склонений и филологической проверки

tests/                          регрессионные тесты

```



Старые экспериментальные модули `src/kp_document_bot` и `src/n8n_*.py` удалены из актуальной ветки. Если понадобится n8n-интеграция, ее лучше проектировать заново вокруг текущих API и job-состояний.



## Важные ограничения текущей архитектуры



- Долгие задачи запускаются в фоне внутри процесса FastAPI.

- Состояние рабочих сессий, auth, клиенты и события хранятся в **PostgreSQL**; бинарные файлы job — в **MinIO (S3)**; `/app/tmp` используется как локальная рабочая папка для генерации.

- Для высокой нагрузки лучше вынести генерацию, проверку документов и отправку в отдельный worker с очередью задач.

- Большие отчеты и архивы могут заметно замедлять ответы API, поэтому для продовой эксплуатации нужна регулярная очистка временных файлов.



## Что не коммитить



В git не должны попадать:



- `.env.docker`;

- `.venv/`;

- `storage/`;

- `logs/`;

- `data/` (runtime-данные);

- `tmp/`, `tmp*/`, `pytest-cache-files-*/`;

- временные Excel-файлы и выгрузки отчетов;

- SQLite/runtime memory files (`*.db`, `*.sqlite`, `*.sqlite3`, `*.db-wal`, `*.db-shm`);

- архивы с результатами отправок;

- локальные скриншоты и промежуточные выгрузки.



Если Excel-справочник нужен для работы сервиса или парсера, его нужно хранить как operational asset или загружать из защищенного хранилища. Не добавляйте новые runtime-копии Excel/SQLite в репозиторий; перед коммитом проверяйте `git ls-files "*.xlsx" "*.db" "*.sqlite" "*.sqlite3"`.
