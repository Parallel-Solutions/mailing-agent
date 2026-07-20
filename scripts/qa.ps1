param(
    [Parameter(Position = 0)]
    [ValidateSet("full", "unit", "e2e", "frontend")]
    [string]$Command = "full"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "artifacts/qa" | Out-Null

$failed = $false
function Run-Step {
    param([string]$Name, [scriptblock]$Block)
    Write-Host "==> $Name"
    try {
        & $Block
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
            throw "exit code $LASTEXITCODE"
        }
        Write-Host "OK: $Name"
    } catch {
        Write-Host "FAIL: $Name :: $_"
        $script:failed = $true
    }
}

switch ($Command) {
    "frontend" {
        Push-Location frontend
        Run-Step "frontend lint/typecheck" { npm run typecheck }
        Run-Step "frontend unit tests" { npm run test }
        Run-Step "frontend production build" { npm run build }
        Pop-Location
    }
    "unit" {
        Run-Step "backend unit/integration (docker test)" {
            docker compose -p mailing-agent-test -f docker-compose.test.yml run --rm test
        }
    }
    "e2e" {
        Run-Step "playwright smoke" { npm run e2e:test:smoke }
        Run-Step "playwright email" { npm run e2e:test:email }
    }
    "full" {
        Push-Location frontend
        Run-Step "1. frontend lint/typecheck" { npm run typecheck }
        Run-Step "2. frontend unit tests" { npm run test }
        Pop-Location

        Run-Step "3. backend tests" {
            docker compose -p mailing-agent-test -f docker-compose.test.yml run --rm test
        }

        Run-Step "4. playwright smoke" { npm run e2e:test:smoke }
        Run-Step "5. playwright email" { npm run e2e:test:email }
    }
}

if ($failed) {
    Write-Host "QA FAILED"
    exit 1
}
Write-Host "QA PASSED"
exit 0
