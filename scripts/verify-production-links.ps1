# Verify production link infrastructure for email-chain campaigns.
# Usage (on server, from repo root):
#   .\scripts\verify-production-links.ps1
# Optional:
#   .\scripts\verify-production-links.ps1 -PublicBaseUrl "https://offer.parresh.ru" -CampaignId "627393ed-0b73-4a7d-a14a-3181f54c553a"

param(
    [string]$PublicBaseUrl = "https://offer.parresh.ru",
    [string]$CampaignId = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "=== Health ==="
curl.exe -sf "$PublicBaseUrl/health" | Write-Output

Write-Host "`n=== Postgres volume (expect pgdata, database mailing) ==="
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps postgres
$pgVolume = docker inspect mailing-agent-postgres-1 --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' 2>$null
if ($pgVolume) {
    Write-Host "Postgres volume: $pgVolume"
    if ($pgVolume -match "test") {
        Write-Warning "Postgres may be on a test volume ($pgVolume). Restore dev/prod stack with dev.ps1 start."
    }
} else {
    Write-Warning "Could not detect postgres volume name."
}

Write-Host "`n=== PUBLIC_BASE_URL in running containers ==="
foreach ($svc in @("app", "worker")) {
    $val = docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T $svc printenv PUBLIC_BASE_URL 2>$null
    Write-Host "$svc PUBLIC_BASE_URL=$val"
}

if ($CampaignId) {
    Write-Host "`n=== Chain tokens for campaign $CampaignId (last 10) ==="
    $sql = @"
SELECT token, recipient_id, clicked_at, send_status, created_at
FROM campaign_chain_tokens
WHERE campaign_id = '$CampaignId'
ORDER BY created_at DESC
LIMIT 10;
"@
    docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres `
        psql -U mailing -d mailing -c $sql
}

Write-Host "`n=== RuSender checklist (manual) ==="
Write-Host "1. In RuSender dashboard: disable click-tracking for transactional/API sends if available."
Write-Host "2. Set RUSENDER_TRACK_LINKS=0 in .env.docker (default)."
Write-Host "3. Webhook URL: $PublicBaseUrl/api/webhooks/rusender/{RUSENDER_WEBHOOK_TOKEN}"
Write-Host "4. Resend affected campaigns after deploy — old email links keep old tokens."
