import { describe, expect, it } from 'vitest';
import {
  buildCampaignReturnState,
  campaignComposerPath,
  resolveCampaignReturnTarget,
} from './campaignNavigation';

describe('campaign navigation context', () => {
  it('builds a canonical composer path', () => {
    expect(campaignComposerPath('draft id')).toBe('/campaigns/new?id=draft%20id');
  });

  it('preserves the exact wizard location for a related page', () => {
    expect(buildCampaignReturnState('draft-1', '/campaigns/new', '?id=draft-1&step=1')).toEqual({
      campaignId: 'draft-1',
      returnTo: '/campaigns/new?id=draft-1&step=1',
    });
  });

  it('prefers an explicit safe return path', () => {
    expect(resolveCampaignReturnTarget({
      campaignId: 'draft-1',
      returnTo: '/campaigns/new?id=draft-1&step=2',
    }, 'draft-2')).toEqual({
      campaignId: 'draft-1',
      path: '/campaigns/new?id=draft-1&step=2',
    });
  });

  it('falls back to the active draft and rejects unrelated return paths', () => {
    expect(resolveCampaignReturnTarget({ returnTo: '/profile' }, 'draft-2')).toEqual({
      campaignId: 'draft-2',
      path: '/campaigns/new?id=draft-2',
    });
    expect(resolveCampaignReturnTarget(null, null)).toBeNull();
  });
});
