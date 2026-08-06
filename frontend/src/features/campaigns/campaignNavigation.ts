export type CampaignReturnState = {
  campaignId?: string;
  returnTo?: string;
};

export function campaignComposerPath(campaignId: string): string {
  return `/campaigns/new?id=${encodeURIComponent(campaignId)}`;
}

export function buildCampaignReturnState(
  campaignId: string,
  pathname: string,
  search: string,
): CampaignReturnState {
  return {
    campaignId,
    returnTo: `${pathname}${search}`,
  };
}

export function resolveCampaignReturnTarget(
  locationState: unknown,
  activeCampaignId?: string | null,
): { campaignId: string; path: string } | null {
  const state = locationState && typeof locationState === 'object'
    ? (locationState as CampaignReturnState)
    : null;
  const campaignId = String(state?.campaignId || activeCampaignId || '').trim();
  if (!campaignId) return null;

  const returnTo = String(state?.returnTo || '');
  const safeReturnTo = returnTo === '/campaigns/new' || returnTo.startsWith('/campaigns/new?');
  return {
    campaignId,
    path: safeReturnTo ? returnTo : campaignComposerPath(campaignId),
  };
}
