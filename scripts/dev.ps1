param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "reset", "stop", "seed", "status")]
    [string]$Command = "start"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-DevCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    & docker compose -f docker-compose.yml -f docker-compose.dev.yml @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Wait-Health {
    param([int]$TimeoutSec = 300)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            $resp = Invoke-WebRequest -Uri "http://localhost:9806/health" -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -eq 200) { return }
        } catch {
            Start-Sleep -Seconds 5
        }
    } while ((Get-Date) -lt $deadline)
    throw "Healthcheck timed out for http://localhost:9806/health"
}

function Show-Access {
    Write-Host ""
    Write-Host "Application:  http://localhost:9806"
    Write-Host "Mailpit:      http://localhost:8025"
    Write-Host "Login:        demo"
    Write-Host "Password:     demo-pass-123"
    Write-Host "Legacy UI:    http://localhost:9806/legacy"
    Write-Host ""
}

switch ($Command) {
    "start" {
        Invoke-DevCompose up -d --build
        Wait-Health
        Invoke-DevCompose exec -T app .venv/bin/python -c "from src.campaigns.seed import seed_demo_data; print(seed_demo_data(force=False))"
        Show-Access
    }
    "reset" {
        Invoke-DevCompose down -v --remove-orphans
        Invoke-DevCompose up -d --build --force-recreate
        Wait-Health
        Invoke-DevCompose exec -T app .venv/bin/python -c "from src.campaigns.seed import seed_demo_data; print(seed_demo_data(force=True))"
        Show-Access
    }
    "stop" {
        Invoke-DevCompose down
    }
    "seed" {
        Invoke-DevCompose exec -T app .venv/bin/python -c "from src.campaigns.seed import seed_demo_data; print(seed_demo_data(force=True))"
        Show-Access
    }
    "status" {
        Invoke-DevCompose ps
        Show-Access
    }
}
