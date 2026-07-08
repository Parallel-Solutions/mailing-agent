#Requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$env:DOCUMENTS_WORKER_MAX_PROCESSES = "8"
$env:SENDER_WORKER_MAX_PROCESSES = "8"
$env:USER_WORKER_MAX_PROCESSES_PER_TASK = "8"
$env:USER_INPROCESS_MAX_TASKS = "8"

Write-Host "Recreating app to apply worker limits ..."
docker compose up -d app
for ($i = 0; $i -lt 30; $i++) {
    if ((docker compose ps app --format "{{.Health}}") -eq "healthy") { break }
    Start-Sleep -Seconds 5
}

docker compose exec app rm -f tests/e2e/out/e2e_report.json tests/e2e/out/e2e_report.csv tests/e2e/out/e2e_state.json 2>$null
Get-ChildItem -Path "tmp\storage\jobs" -Recurse -Filter ".sender.run.lock" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

docker compose exec `
    -e RUN_REAL_E2E=1 `
    -e E2E_BASE_URL=http://127.0.0.1:9806 `
    -e E2E_PARALLEL_JOBS=6 `
    -e E2E_SEND_PAUSE_SECONDS=1 `
    app .venv/bin/python -m tests.e2e.run_send_matrix
