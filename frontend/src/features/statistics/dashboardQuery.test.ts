import { describe, expect, it } from 'vitest';
import { managerDashboardParams, managerDashboardQueryKey } from './dashboardQuery';
import { nextStatisticsRefreshState } from './hooks/useStatisticsState';

describe('statistics dashboard refresh', () => {
  it('uses one shared query key for dashboard observers', () => {
    const params = { campaign: 'job-1' };
    expect(managerDashboardQueryKey(params, 3)).toEqual([
      'stats-dashboard',
      params,
      3,
    ]);
  });

  it('does not refresh providers during an automatic refresh', () => {
    const next = nextStatisticsRefreshState({ nonce: 2, providerNonce: 2 });
    expect(next).toEqual({ nonce: 3, providerNonce: null });
    expect(managerDashboardParams({}, false).refresh).toBeUndefined();
  });

  it('marks only an explicit manual refresh as a provider refresh', () => {
    const next = nextStatisticsRefreshState(
      { nonce: 2, providerNonce: null },
      { provider: true },
    );
    expect(next).toEqual({ nonce: 3, providerNonce: 3 });
    expect(managerDashboardParams({}, true).refresh).toBe(true);
  });
});
