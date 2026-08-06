import { describe, expect, it } from 'vitest';
import { isStatsTabKey, PAGE_TITLES, STATS_TABS } from './constants';

describe('statistics tabs', () => {
  it('exposes the audience management tab used by onboarding', () => {
    expect(isStatsTabKey('audiences')).toBe(true);
    expect(STATS_TABS).toContainEqual({ key: 'audiences', label: 'Аудитории' });
    expect(PAGE_TITLES.audiences).toBe('Аудитории');
  });

  it('keeps campaign drafts separate from launched campaigns', () => {
    expect(isStatsTabKey('campaign-list')).toBe(true);
    expect(isStatsTabKey('draft-list')).toBe(true);
    expect(STATS_TABS).toContainEqual({ key: 'campaign-list', label: 'Рассылки' });
    expect(STATS_TABS).toContainEqual({ key: 'draft-list', label: 'Черновики' });
    expect(PAGE_TITLES['draft-list']).toBe('Черновики рассылок');
  });
});
