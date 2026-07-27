import type { Campaign } from '@/api/types';

const ACTIVE_CAMPAIGN_STATUSES = new Set(['scheduled', 'running', 'paused']);

export function shouldPollCampaign(status?: string): boolean {
  return Boolean(status && ACTIVE_CAMPAIGN_STATUSES.has(status));
}

export function canCampaignAction(campaign: Campaign | undefined, action: string): boolean {
  return Boolean(campaign?.allowed_actions?.includes(action));
}

export function campaignProgressLabel(campaign: Campaign): string {
  return `${campaign.processed_count ?? 0}/${campaign.total_count ?? 0}`;
}
