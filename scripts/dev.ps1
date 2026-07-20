param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "reset", "stop", "seed", "status")]
    [string]$Command = "start"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-DevCompose {
    # Pass compose args as an explicit array so PowerShell does not bind -d/-T as common params.
    param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)
    & docker compose -f docker-compose.yml -f docker-compose.dev.yml @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSec = 300,
        [scriptblock]$Validate = $null
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            $resp = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -eq 200) {
                if ($null -eq $Validate -or (& $Validate $resp)) {
                    return
                }
            }
        } catch {
            # retry until deadline
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    throw "Healthcheck timed out for $Uri"
}

function Wait-Health {
    param([int]$TimeoutSec = 300)
    Wait-HttpOk -Uri "http://localhost:9806/health" -TimeoutSec $TimeoutSec
    Wait-HttpOk -Uri "http://localhost:9806/ready" -TimeoutSec ([Math]::Max(30, [int]($TimeoutSec / 2))) -Validate {
        param($resp)
        $body = $resp.Content | ConvertFrom-Json
        return ($body.status -eq 'ok')
    }
    Wait-HttpOk -Uri "http://localhost:8025/api/v1/info" -TimeoutSec 60
    $deadline = (Get-Date).AddSeconds(120)
    do {
        $workerId = & docker compose -f docker-compose.yml -f docker-compose.dev.yml ps -q worker 2>$null
        $workerHealthy = if ($workerId) {
            docker inspect --format='{{.State.Health.Status}}' $workerId 2>$null
        } else { '' }
        if ($workerHealthy -eq 'healthy') { return }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    throw "Worker healthcheck timed out (status=$workerHealthy)"
}

function Show-Access {
    Write-Host ""
    Write-Host "Application:  http://localhost:9806"
    Write-Host "Mailpit:      http://localhost:8025"
    Write-Host "Login:        demo"
    Write-Host "Password:     demo-pass-123"
    Write-Host "Seed demo:    .\scripts\dev.ps1 seed"
    Write-Host ""
}

switch ($Command) {
    "start" {
        Invoke-DevCompose -ComposeArgs @('up', '--detach', '--build')
        Wait-Health
        Show-Access
    }
    "reset" {
        Invoke-DevCompose -ComposeArgs @('down', '-v', '--remove-orphans')
        Invoke-DevCompose -ComposeArgs @('up', '--detach', '--build', '--force-recreate')
        Wait-Health
        Invoke-DevCompose -ComposeArgs @('exec', '-T', 'app', '.venv/bin/python', '-c', 'from src.campaigns.seed import seed_demo_data; print(seed_demo_data(force=True))')
        Show-Access
    }
    "stop" {
        Invoke-DevCompose -ComposeArgs @('down')
    }
    "seed" {
        Invoke-DevCompose -ComposeArgs @('exec', '-T', 'app', '.venv/bin/python', '-c', 'from src.campaigns.seed import seed_demo_data; print(seed_demo_data(force=True))')
        Show-Access
    }
    "status" {
        Invoke-DevCompose -ComposeArgs @('ps')
        Show-Access
    }
}
