import { describe, expect, it } from 'vitest';
import {
  campaignProgressLabel,
  canCampaignAction,
  shouldPollCampaign,
} from './campaignLifecycle';

describe('campaign lifecycle UI helpers', () => {
  it('uses processed recipients for the progress label', () => {
    expect(
      campaignProgressLabel({
        id: 'campaign-1',
        name: 'Complete with errors',
        status: 'completed_with_errors',
        processed_count: 1021,
        total_count: 1021,
        success_count: 965,
      }),
    ).toBe('1021/1021');
  });

  it('does not expose active actions for a terminal campaign', () => {
    const campaign = {
      id: 'campaign-1',
      name: 'Complete',
      status: 'completed',
      allowed_actions: ['duplicate', 'archive'],
    };
    expect(canCampaignAction(campaign, 'pause')).toBe(false);
    expect(canCampaignAction(campaign, 'cancel')).toBe(false);
    expect(canCampaignAction(campaign, 'duplicate')).toBe(true);
  });

  it('polls only active campaign states', () => {
    expect(shouldPollCampaign('running')).toBe(true);
    expect(shouldPollCampaign('paused')).toBe(true);
    expect(shouldPollCampaign('completed_with_errors')).toBe(false);
  });
});
