#Requires -Version 5.1
<#
.SYNOPSIS
  Windows helper for Docker-only Playwright E2E.
.EXAMPLE
  .\scripts\e2e.ps1 full
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $Root

# Separate compose project so E2E never rewrites local dev app (mailing on :9806).
$Compose = @(
  'docker', 'compose',
  '-p', 'mailing-agent-e2e',
  '-f', 'docker-compose.yml',
  '-f', 'docker-compose.e2e.yml',
  '--env-file', '.env.e2e'
)

$ReportPath = Join-Path $Root 'artifacts\playwright\report\index.html'
$Artifacts = Join-Path $Root 'artifacts\playwright'

function Ensure-EnvFile {
  $example = Join-Path $Root '.env.e2e.example'
  $envFile = Join-Path $Root '.env.e2e'
  if (-not (Test-Path $envFile)) {
    if (-not (Test-Path $example)) {
      throw '.env.e2e.example is missing'
    }
    Copy-Item $example $envFile
    Write-Host "Created .env.e2e from .env.e2e.example" -ForegroundColor Yellow
  }
  if (-not (Test-Path (Join-Path $Root '.env.docker'))) {
    throw '.env.docker is required for the base stack. Copy .env.docker.example first.'
  }
}

function Invoke-Compose {
  # Explicit array: PowerShell must not bind -d/-T as common parameters.
  param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)
  & @Compose @ComposeArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed with exit code $LASTEXITCODE"
  }
}

function Show-ReportHint {
  Write-Host ""
  Write-Host "HTML report: $ReportPath" -ForegroundColor Cyan
  Write-Host "Artifacts:   $Artifacts" -ForegroundColor Cyan
}

function Show-FailureLogs {
  Write-Host "---- recent app logs ----" -ForegroundColor Yellow
  try { Invoke-Compose -ComposeArgs @('logs', '--no-color', '--tail', '80', 'app') } catch { Write-Host $_ }
  Write-Host "---- recent mailpit logs ----" -ForegroundColor Yellow
  try { Invoke-Compose -ComposeArgs @('logs', '--no-color', '--tail', '40', 'mailpit') } catch { Write-Host $_ }
}

function Ensure-Dirs {
  @(
    'artifacts\playwright\report',
    'artifacts\playwright\results',
    'artifacts\playwright\screenshots',
    'artifacts\playwright\videos',
    'artifacts\playwright\traces',
    'artifacts\playwright\auth'
  ) | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $_) | Out-Null
  }
}

function Cmd-Build { Invoke-Compose -ComposeArgs @('--profile', 'playwright', 'build', 'playwright') }
function Cmd-Up { Invoke-Compose -ComposeArgs @('up', '--detach', '--build') }
function Cmd-Down { Invoke-Compose -ComposeArgs @('--profile', 'playwright', 'down') }
function Cmd-Clean { Invoke-Compose -ComposeArgs @('--profile', 'playwright', 'down', '-v', '--remove-orphans') }

function Cmd-Test {
  param([string[]]$PwArgs)
  try {
    $composeArgs = @('--profile', 'playwright', 'run', '--rm', 'playwright') + @($PwArgs)
    Invoke-Compose -ComposeArgs $composeArgs
  } catch {
    Show-FailureLogs
    Show-ReportHint
    throw
  }
  Show-ReportHint
}

function Cmd-Report {
  if (-not (Test-Path $ReportPath)) {
    throw "Report not found: $ReportPath — run tests first."
  }
  Write-Host "Opening $ReportPath"
  Start-Process $ReportPath
}

function Cmd-Full {
  Ensure-Dirs
  Write-Host "== e2e:clean volumes for isolated DB ==" -ForegroundColor Green
  # Keep named volumes of prod stack if user wants; for full we recreate e2e overlay services.
  Cmd-Down
  Cmd-Up
  Write-Host "== waiting for health ==" -ForegroundColor Green
  $deadline = (Get-Date).AddMinutes(5)
  $appHealthy = ''
  $workerHealthy = ''
  do {
    Start-Sleep -Seconds 3
    $appId = & @Compose @('ps', '-q', 'app') 2>$null
    $workerId = & @Compose @('ps', '-q', 'worker') 2>$null
    $appHealthy = if ($appId) { docker inspect --format='{{.State.Health.Status}}' $appId 2>$null } else { '' }
    $workerHealthy = if ($workerId) { docker inspect --format='{{.State.Health.Status}}' $workerId 2>$null } else { '' }
    if ($appHealthy -eq 'healthy' -and $workerHealthy -eq 'healthy') { break }
  } while ((Get-Date) -lt $deadline)
  if ($appHealthy -ne 'healthy' -or $workerHealthy -ne 'healthy') {
    Show-FailureLogs
    throw "stack not healthy (app=$appHealthy worker=$workerHealthy)"
  }

  Write-Host "== chromium suite ==" -ForegroundColor Green
  Cmd-Test @('npx', 'playwright', 'test', '--project=chromium')

  Write-Host "== firefox smoke ==" -ForegroundColor Green
  Cmd-Test @('npx', 'playwright', 'test', '--project=firefox-smoke')

  Write-Host "== webkit smoke ==" -ForegroundColor Green
  Cmd-Test @('npx', 'playwright', 'test', '--project=webkit-smoke')

  Write-Host "E2E full suite completed successfully." -ForegroundColor Green
  Show-ReportHint
}

Ensure-EnvFile
Ensure-Dirs

$cmd = if ($args.Count -gt 0) { $args[0].ToLowerInvariant() } else { 'test' }
$rest = @()
if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }

switch ($cmd) {
  'build' { Cmd-Build }
  'up' { Cmd-Up }
  'down' { Cmd-Down }
  'clean' { Cmd-Clean }
  'test' { Cmd-Test (@('npx', 'playwright', 'test') + $rest) }
  'smoke' { Cmd-Test @('npx', 'playwright', 'test', '--project=chromium', '--grep', '@smoke') }
  'email' { Cmd-Test @('npx', 'playwright', 'test', '--grep', '@email') }
  'visual' { Cmd-Test @('npx', 'playwright', 'test', '--grep', '@visual', '--project=chromium') }
  'update-snapshots' { Cmd-Test @('npx', 'playwright', 'test', '--grep', '@visual', '--project=chromium', '--update-snapshots') }
  'headed' { Cmd-Test @('npm', 'run', 'test:headed') }
  'debug' { Cmd-Test @('npm', 'run', 'test:debug') }
  'report' { Cmd-Report }
  'full' { Cmd-Full }
  default {
    Write-Host @"
Usage: .\scripts\e2e.ps1 <command>
  build | up | test | smoke | email | visual | update-snapshots
  headed | debug | report | down | clean | full
"@
    exit 2
  }
}
