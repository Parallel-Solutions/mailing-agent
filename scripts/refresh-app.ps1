# Пересоздаёт контейнер app с актуальными volume mounts и проверяет шаблон.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "Recreating mailing-agent app container..."
docker compose up -d --force-recreate app

Write-Host "Waiting for healthcheck..."
$deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 3
    $status = docker compose ps app --format "{{.Status}}"
    if ($status -match "healthy") {
        break
    }
} while ((Get-Date) -lt $deadline)

$providerCount = docker compose exec app sh -c "grep -c 'settings-smtp-provider' /app/templates/index.html || true"
$discoverCount = docker compose exec app sh -c "grep -c 'smtpRequiresManual' /app/templates/index.html || true"

Write-Host "Container status: $status"
Write-Host "Template check: settings-smtp-provider=$providerCount, smtpRequiresManual=$discoverCount"
Write-Host "Open http://localhost:9806/ and press Ctrl+Shift+R"
