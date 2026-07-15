param(
    [switch]$SkipDocker,
    [switch]$SkipBitrix
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not $SkipDocker) {
    docker compose up -d --build app worker
    docker compose exec app printenv SMTP_CREDENTIALS_KEY | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "SMTP_CREDENTIALS_KEY is missing in .env.docker"
    }
}

Write-Host "== Unit/API tests =="
docker compose -f docker-compose.test.yml run --rm `
    -e SMTP_CREDENTIALS_KEY=22so540-u99Ath3we93faCovEet48m0vvvEJ5R2UwDY= `
    test .venv/bin/python -m unittest `
    tests.test_report_export.AutoCallExportTests `
    tests.test_report_export.AutoCallReportExportTests `
    tests.test_smtp_mailboxes `
    tests.test_api_request_validation.SenderRequestValidationTests `
    tests.test_task_queue -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== Seed acceptance job =="
docker compose exec app .venv/bin/python -m tests.ui.fixtures_acceptance
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== Playwright acceptance UI =="
docker compose exec `
    -e ACCEPTANCE_UI_E2E=1 `
    -e E2E_BASE_URL=http://127.0.0.1:9806 `
    -e E2E_USERNAME=admin `
    -e E2E_PASSWORD=$env:APP_PASSWORD `
    app .venv/bin/python -m unittest `
        tests.ui.test_auto_call_export_ui `
        tests.ui.test_smtp_mailboxes_ui `
        tests.ui.test_scheduled_send_ui -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipBitrix) {
    Write-Host "== Post to Bitrix =="
    python scripts/post-acceptance-to-bitrix.py --task-ids 109652,109636,109651
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "Acceptance run completed."
