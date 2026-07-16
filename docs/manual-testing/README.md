# Ручное тестирование CampaignFlow

## Требования

- Docker Desktop
- PowerShell
- Порты 9806 и 8025 свободны

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
