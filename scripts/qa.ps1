param(
    [Parameter(Position = 0)]
    [ValidateSet("fast", "gate", "full", "unit", "e2e", "frontend")]
    [string]$Command = "gate"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "artifacts/qa" | Out-Null

$E2eCompose = @(
    'docker', 'compose',
    '-p', 'mailing-agent-e2e',
    '-f', 'docker-compose.yml',
    '-f', 'docker-compose.e2e.yml',
    '--env-file', '.env.e2e'
)

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

function Ensure-E2eEnv {
    if (-not (Test-Path '.env.e2e')) { Copy-Item '.env.e2e.example' '.env.e2e' }
    if (-not (Test-Path '.env.docker')) { Copy-Item '.env.docker.example' '.env.docker' }
}

function Start-TestInfra {
    docker compose -p mailing-agent-test -f docker-compose.test.yml up -d postgres minio minio-init
    if ($LASTEXITCODE -ne 0) { throw "test infra start failed" }
}

function Run-BackendTests {
    param([string[]]$ExtraArgs = @())
    Start-TestInfra
    $args = @(
        'compose', '-p', 'mailing-agent-test', '-f', 'docker-compose.test.yml',
        'run', '--rm', '--no-deps', 'test'
    ) + $ExtraArgs
    docker @args
}

function Run-FastBackend {
    Run-BackendTests @(
        '.venv/bin/python', '-m', 'unittest',
        'tests.test_db_migration_recovery',
        'tests.test_campaign_v1_api.CampaignV1ApiTests.test_create_update_schedule_launch_pause',
        'tests.test_campaign_v1_api.CampaignV1ApiTests.test_variable_mapping_import_suggest_save_and_launch_gate',
        'tests.test_route_blocking_contract.RouteBlockingContractTests.test_only_streaming_routes_remain_async',
        'tests.test_template_render_service.TemplateRenderServiceTests.test_launch_schedules_pre_generate_task',
        '-v'
    )
}

function Restore-DevStack {
    & (Join-Path $Root 'scripts\dev.ps1') start
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
        Run-Step "backend unit/integration (docker test)" { Run-BackendTests }
    }
    "e2e" {
        Ensure-E2eEnv
        Run-Step "playwright smoke" { npm run e2e:test:smoke }
        Run-Step "playwright email" { npm run e2e:test:email }
    }
    "fast" {
        Run-Step "targeted backend (6 tests)" { Run-FastBackend }
        Ensure-E2eEnv
        Run-Step "e2e stack (no rebuild)" { npm run e2e:up:fast }
        Run-Step "playwright smoke" { npm run e2e:test:smoke }
        Run-Step "campaign-flow email (chromium)" {
            docker @E2eCompose @('--profile', 'playwright', 'run', '--rm', 'playwright',
                'npx', 'playwright', 'test', '--project=chromium',
                'tests/campaigns/campaign-flow.spec.ts', '-g', 'launch campaign')
        }
        Run-Step "restore dev stack" { Restore-DevStack }
    }
    "gate" {
        Push-Location frontend
        Run-Step "frontend typecheck" { npm run typecheck }
        Run-Step "frontend unit tests" { npm run test }
        Pop-Location

        Run-Step "backend tests" { Run-BackendTests }
        Ensure-E2eEnv
        Run-Step "e2e stack (no rebuild)" { npm run e2e:up:fast }
        Run-Step "playwright smoke" { npm run e2e:test:smoke }
        Run-Step "e2e down" { npm run e2e:down }
        Run-Step "restore dev stack" { Restore-DevStack }
    }
    "full" {
        Push-Location frontend
        Run-Step "frontend typecheck" { npm run typecheck }
        Run-Step "frontend unit tests" { npm run test }
        Pop-Location

        Run-Step "backend tests" { Run-BackendTests }
        Ensure-E2eEnv
        Run-Step "e2e stack (no rebuild)" { npm run e2e:up:fast }
        Run-Step "playwright smoke" { npm run e2e:test:smoke }
        Run-Step "playwright email (chromium)" { npm run e2e:test:email }
        Run-Step "e2e down" { npm run e2e:down }
        Run-Step "restore dev stack" { Restore-DevStack }
    }
}

if ($failed) {
    Write-Host "QA FAILED"
    exit 1
}
Write-Host "QA PASSED"
exit 0
