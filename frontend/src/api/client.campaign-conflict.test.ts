import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiRequest } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiRequest campaign lifecycle errors', () => {
  it('preserves campaign id and status from a structured conflict', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: 'campaign_not_draft',
              title: 'Campaign state conflict',
              message: 'Only a draft campaign can be launched.',
              hint: 'Create a new draft.',
              campaign_id: 'campaign-1',
              campaign_status: 'completed',
            },
          }),
          {
            status: 409,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    );

    const error = await apiRequest('/api/v1/campaigns/campaign-1/launch').catch(
      (reason) => reason,
    );

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) {
      throw new Error('Expected apiRequest to reject with ApiError');
    }
    expect(error.status).toBe(409);
    expect(error.payload).toMatchObject({
      code: 'campaign_not_draft',
      campaign_id: 'campaign-1',
      campaign_status: 'completed',
    });
  });
});
