import { afterEach, describe, expect, it, vi } from 'vitest';

import { statisticsApi } from './statistics';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('statisticsApi campaign period', () => {
  it('passes the selected period to every campaign statistics endpoint', async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: 'ok', result: {} }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const period = { period_from: '2026-05-01', period_to: '2026-05-03' };

    await statisticsApi.campaignAnalytics('job-1', period);
    await statisticsApi.campaignAttempts('job-1', { ...period, page: 2 });
    await statisticsApi.campaignFullAnalytics('job-1', period);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/sender/campaign-analytics/job-1?period_from=2026-05-01&period_to=2026-05-03',
      expect.objectContaining({ credentials: 'include' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/sender/campaign-attempts/job-1?period_from=2026-05-01&period_to=2026-05-03&page=2',
      expect.objectContaining({ credentials: 'include' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/sender/campaign-full-analytics/job-1?period_from=2026-05-01&period_to=2026-05-03',
      expect.objectContaining({ credentials: 'include' }),
    );
  });
});
