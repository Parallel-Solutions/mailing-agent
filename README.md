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



Docker Compose поднимает:

- `app` — FastAPI-приложение на Python;
- `postgres` — PostgreSQL (строковые/state-данные, auth, клиенты, события);
- `minio` — S3-совместимое хранилище файлов (xlsx, docx, pdf, шаблоны);
- `redis` — кэш и progress streams парсера;
- `gotenberg`, `gotenberg-2` — внутренние сервисы для DOCX → PDF конвертации.

Данные приложения:
- **PostgreSQL** — users/sessions, состояние агентов, owner, consents, события (`*.jsonl`), строки клиентов;
- **MinIO (S3)** — бинарные файлы job (`input/`, `templates/`, `output/`, `consents/`, отчёты);
- **локальный `/app/tmp`** — рабочая папка для генерации документов (синхронизируется с S3 на границах upload/worker/finalize).



Gotenberg не публикуется наружу и доступен только контейнеру приложения по адресам `http://gotenberg:3000` и `http://gotenberg-2:3000`. PDF-конвертация настроена через Gotenberg.



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

docker compose logs -f app

```



Тесты:

**Unit/integration** (без реальной отправки, Postgres + MinIO):

```bash
docker compose -f docker-compose.test.yml run --rm test
```

Локально (из корня проекта, с активированным `.venv`):

```bash
python -m tests
```

**E2E send matrix** (реальная отправка через RuSender, 35×4=140 сценариев, несколько часов):

```bash
cp .env.e2e.example .env.docker   # заполнить RuSender и auth
docker compose up -d
./scripts/run-e2e-matrix.ps1      # Windows
# или
RUN_REAL_E2E=1 docker compose exec app .venv/bin/python -m tests.e2e.run_send_matrix
```

Подробности: [`tests/e2e/README.md`](tests/e2e/README.md).



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

- `SENDER_DELAY_SECONDS` — фиксированная пауза между SMTP-письмами; `SENDER_DELAY_MIN_SECONDS`/`SENDER_DELAY_MAX_SECONDS` задают случайную паузу, например `179` и `247` для диапазона 2:59–4:07.



Файл `.env.docker` не должен попадать в git.



## Структура проекта



```text

main.py                         FastAPI-приложение и API-роуты

templates/index.html            веб-интерфейс

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
