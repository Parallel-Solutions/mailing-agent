#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Required = @(
    "RUN_REAL_E2E",
    "RUSENDER_API_KEY",
    "RUSENDER_SENDER_EMAIL",
    "RUSENDER_WEBHOOK_SECRET",
    "PUBLIC_BASE_URL"
)

$Missing = @()
foreach ($Name in $Required) {
    $Value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Missing += $Name
    }
}

if ($Missing.Count -gt 0) {
    Write-Error ("Missing required environment variables: " + ($Missing -join ", "))
}

if ($env:RUN_REAL_E2E -ne "1") {
    Write-Error "Set RUN_REAL_E2E=1 to run the real send matrix."
}

Write-Host "Restarting app to clear in-memory worker slots ..."
docker compose restart app

Write-Host "Waiting for app health ..."
for ($i = 0; $i -lt 30; $i++) {
    $status = docker compose ps app --format "{{.Health}}" 2>$null
    if ($status -eq "healthy") { break }
    Start-Sleep -Seconds 5
}

Write-Host "Clearing E2E report/state inside app container ..."
docker compose exec app rm -f tests/e2e/out/e2e_report.json tests/e2e/out/e2e_state.json 2>$null

Get-ChildItem -Path "tmp\storage\jobs" -Recurse -Filter ".sender.run.lock" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

$E2EBase = if ($env:E2E_BASE_URL) { $env:E2E_BASE_URL } else { "http://localhost:9806" }

docker compose exec `
    -e RUN_REAL_E2E=1 `
    -e E2E_BASE_URL=$E2EBase `
    app .venv/bin/python -m tests.e2e.run_send_matrix
