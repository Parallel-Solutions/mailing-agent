# Production health and disk audit for mailing-agent.
# Usage (on server, from repo root):
#   .\scripts\prod-audit.ps1
# Optional:
#   .\scripts\prod-audit.ps1 -PublicBaseUrl "https://offer.parresh.ru" -DiskWarnPercent 85

param(
    [string]$PublicBaseUrl = "https://offer.parresh.ru",
    [int]$DiskWarnPercent = 85
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "=== $Title ==="
}

function Get-HeadRevision() {
    $files = Get-ChildItem -Path (Join-Path $repoRoot "migrations/versions") -Filter "*.py" -ErrorAction SilentlyContinue
    if (-not $files) { return $null }
    $revisions = @{}
    foreach ($file in $files) {
        $content = Get-Content -Raw -Path $file.FullName
        if ($content -match 'revision\s*=\s*"([^"]+)"') {
            $rev = $Matches[1]
            $down = $null
            if ($content -match 'down_revision\s*=\s*"([^"]+)"') {
                $down = $Matches[1]
            }
            $revisions[$rev] = $down
        }
    }
    $referenced = [System.Collections.Generic.HashSet[string]]::new([string[]]$revisions.Values | Where-Object { $_ })
    foreach ($rev in $revisions.Keys) {
        if (-not $referenced.Contains($rev)) {
            return $rev
        }
    }
    return ($revisions.Keys | Sort-Object | Select-Object -Last 1)
}

Write-Section "Disk"
if (Get-Command df -ErrorAction SilentlyContinue) {
    $dfLine = df -h / | Select-Object -Last 1
    Write-Host $dfLine
    if ("$dfLine" -match "(\d+)%") {
        $used = [int]$Matches[1]
        if ($used -ge $DiskWarnPercent) {
            Write-Warning "Disk usage ${used}% exceeds warning threshold ${DiskWarnPercent}%."
        }
    }
} else {
    Write-Warning "df not available — run on Linux prod host."
}

Write-Section "Docker disk"
docker system df

Write-Section "Compose services"
docker compose --env-file .env.docker --profile onlyoffice -f docker-compose.yml -f docker-compose.prod.yml ps

Write-Section "Health"
try {
    curl.exe -sf "$PublicBaseUrl/health" | Write-Output
} catch {
    Write-Warning "Public /health failed: $_"
}
try {
    curl.exe -sf "$PublicBaseUrl/ready" | Write-Output
} catch {
    Write-Warning "Public /ready failed: $_"
}
try {
    curl.exe -sf "$PublicBaseUrl/onlyoffice/healthcheck" | Write-Output
} catch {
    Write-Warning "Public OnlyOffice healthcheck failed: $_"
}

Write-Section "App stability"
$restartCount = docker inspect mailing-agent-app-1 --format "{{.RestartCount}}" 2>$null
if ($restartCount) {
    Write-Host "app RestartCount=$restartCount"
    if ([int]$restartCount -gt 5) {
        Write-Warning "app has restarted more than 5 times — investigate crash loop."
    }
}

Write-Section "Alembic"
$dbRev = docker compose -f docker-compose.yml exec -T postgres psql -U mailing -d mailing -t -A -c "SELECT version_num FROM alembic_version;" 2>$null
$headRev = Get-HeadRevision
Write-Host "database alembic_version=$dbRev"
Write-Host "repo head revision=$headRev"
if ($dbRev -and $headRev -and ($dbRev.Trim() -ne $headRev)) {
    Write-Warning "Alembic stamp differs from repo head — check migration drift before deploy."
}

Write-Section "Data directories"
foreach ($path in @("./tmp", "./logs", "./storage")) {
    if (Test-Path $path) {
        $size = (Get-ChildItem -Recurse -Force -ErrorAction SilentlyContinue $path | Measure-Object -Property Length -Sum).Sum
        if ($null -eq $size) { $size = 0 }
        $mb = [math]::Round($size / 1MB, 1)
        Write-Host "${path}: ${mb} MB"
    }
}

Write-Section "Docker volumes"
foreach ($vol in @(
    "mailing-agent_pgdata",
    "mailing-agent_minio-data",
    "mailing-agent_redis-data",
    "mailing-agent_chroma-data",
    "mailing-agent_onlyoffice-data",
    "mailing-agent_onlyoffice-lib",
    "mailing-agent_onlyoffice-logs",
    "mailing-agent_onlyoffice-db"
)) {
    $inspect = docker volume inspect $vol --format "{{.Mountpoint}}" 2>$null
    if ($inspect) {
        Write-Host "$vol -> $inspect"
    }
}

Write-Section "PUBLIC_BASE_URL"
foreach ($svc in @("app", "worker")) {
    $val = docker compose --env-file .env.docker --profile onlyoffice -f docker-compose.yml -f docker-compose.prod.yml exec -T $svc printenv PUBLIC_BASE_URL 2>$null
    Write-Host "$svc PUBLIC_BASE_URL=$val"
}

Write-Section "OnlyOffice"
$onlyofficeState = docker inspect mailing-agent-onlyoffice-1 --format "{{.State.Status}}" 2>$null
$onlyofficeHealth = docker inspect mailing-agent-onlyoffice-1 --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" 2>$null
Write-Host "onlyoffice state=$onlyofficeState health=$onlyofficeHealth"
if ($onlyofficeState -ne "running" -or $onlyofficeHealth -ne "healthy") {
    Write-Warning "OnlyOffice must be running and healthy on production."
}

Write-Section "Optional profiles (should be stopped on prod)"
foreach ($name in @("mailing-agent-gotenberg-2-1")) {
    $state = docker inspect $name --format "{{.State.Status}}" 2>$null
    if ($state -eq "running") {
        Write-Warning "$name is running — consider stopping to save RAM/disk."
    }
}

Write-Host ""
Write-Host "Audit complete."
