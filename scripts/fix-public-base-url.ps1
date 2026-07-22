# Fix PUBLIC_BASE_URL on a production host and restart app + worker.
# Usage (on server, from repo root):
#   .\scripts\fix-public-base-url.ps1
# Optional:
#   .\scripts\fix-public-base-url.ps1 -PublicBaseUrl "https://offer.parresh.ru"

param(
    [string]$PublicBaseUrl = "https://offer.parresh.ru",
    [string]$EnvFile = ".env.docker"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path $EnvFile)) {
    throw "$EnvFile not found in $repoRoot"
}

$content = Get-Content $EnvFile -Raw
if ($content -match '(?m)^PUBLIC_BASE_URL=') {
    $content = [regex]::Replace($content, '(?m)^PUBLIC_BASE_URL=.*$', "PUBLIC_BASE_URL=$PublicBaseUrl")
} else {
    $content = "$content`nPUBLIC_BASE_URL=$PublicBaseUrl`n"
}
Set-Content -Path $EnvFile -Value $content -NoNewline

Write-Host "Updated $EnvFile -> PUBLIC_BASE_URL=$PublicBaseUrl"
Write-Host "Restarting app and worker with production overlay..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d app worker
curl.exe -sf "$PublicBaseUrl/health" | Write-Output
Write-Host "Done. Resend campaign emails so links use the corrected domain."
