# Ручное тестирование CampaignFlow

## Режим: локально = prod-like

Ручная проверка UI всегда через **`dev.ps1`** → БД **`mailing`** (как в проде/на тесте).

| Режим | Команда | БД |
|-------|---------|-----|
| Локальная ручная проверка | `.\scripts\dev.ps1 start` | `mailing` |
| Playwright E2E | `.\scripts\e2e.ps1 …` (проект `mailing-agent-e2e`) | `mailing_e2e` |
| Unit/integration | `docker compose -f docker-compose.test.yml run --rm test` | `mailing_test` |

Не используйте e2e overlay / `mailing_e2e` для ручного тестирования. E2E не должен переписывать app на `:9806`.

## Требования

- Docker Desktop
- PowerShell
- Порты 9806 и 8025 свободны (E2E использует 19806 / 18025)

## Запуск

```powershell
.\scripts\dev.ps1 start
```

Команда соберёт контейнеры, поднимет стек (app, worker, postgres, minio, redis, gotenberg, mailpit), дождётся healthcheck и создаст demo seed.

## Reset

```powershell
.\scripts\dev.ps1 reset
```

Полностью удаляет volumes и поднимает окружение заново с принудительным seed.

## Адреса и credentials

См. [artifacts/manual-testing/ACCESS.md](../../artifacts/manual-testing/ACCESS.md).

## Mailpit

Откройте http://localhost:8025 — там появляются письма от SMTP-подключения Mailpit.

## Логи

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f app worker
```

## Остановка

```powershell
.\scripts\dev.ps1 stop
```

## QA

```powershell
.\scripts\qa.ps1 full
```

Чеклист: [CHECKLIST.md](CHECKLIST.md).
