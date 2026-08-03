import { describe, expect, it } from 'vitest';
import { isStatsTabKey, PAGE_TITLES, STATS_TABS } from './constants';

describe('statistics tabs', () => {
  it('exposes the audience management tab used by onboarding', () => {
    expect(isStatsTabKey('audiences')).toBe(true);
    expect(STATS_TABS).toContainEqual({ key: 'audiences', label: 'Аудитории' });
    expect(PAGE_TITLES.audiences).toBe('Аудитории');
  });
});
