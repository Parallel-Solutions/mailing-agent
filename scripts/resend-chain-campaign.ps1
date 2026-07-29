# Prepare and relaunch an email-chain campaign so recipients get fresh /chain/branch/ tokens.
# Usage (on server, from repo root):
#   .\scripts\resend-chain-campaign.ps1 -CampaignId "627393ed-0b73-4a7d-a14a-3181f54c553a"
#
# Requires: logged-in session cookie or run launch from the CampaignFlow UI after this script resets statuses.

param(
    [Parameter(Mandatory = $true)]
    [string]$CampaignId,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "Resetting send_status for in_chain/sent recipients on campaign $CampaignId ..."
$sql = @"
UPDATE campaign_recipients
SET send_status = 'pending', last_error = NULL
WHERE campaign_id = '$CampaignId'
  AND excluded = false
  AND send_status IN ('in_chain', 'sent', 'failed');
"@

if ($DryRun) {
    Write-Host "[dry-run] Would execute:"
    Write-Host $sql
    exit 0
}

docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres `
    psql -U mailing -d mailing -c $sql

Write-Host @"

Done. Next steps:
1. Deploy latest app + worker (with chain token fix and RUSENDER_TRACK_LINKS=0).
2. In CampaignFlow UI open: /campaigns/$CampaignId
3. Click launch / 'Запустить сейчас' (force now) to enqueue new batches.
4. Send a test email to yourself and verify href points to offer.parresh.ru/chain/branch/{uuid}
   without clicks.clicksends.net wrapper when possible.
5. Click the link — expect 'Спасибо' page, not 'Ссылка не найдена'.

"@
